"""Parser test suite: S-expression core, KiCad schematic (incl. pin transforms and net
building), PCB, SPICE/KiCad netlists, BOM, ZIP safety, and the normalizer."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.models.normalized import PinType
from app.parsers.bom import parse_bom_csv
from app.parsers.kicad_pcb import parse_pcb
from app.parsers.kicad_pro import parse_project_file
from app.parsers.kicad_sch import PlacedSymbol, _transform_pin, parse_schematic
from app.parsers.normalizer import normalize_project
from app.parsers.sexpr import SExprError, find_all, find_first, first_atom, parse_sexpr
from app.parsers.spice import parse_netlist
from app.parsers.zip_safe import UnsafeZipError, safe_extract_zip

FIXTURES = Path(__file__).parent / "fixtures"


# --- sexpr ---------------------------------------------------------------------------------


class TestSExpr:
    def test_basic_nesting(self):
        tree = parse_sexpr('(a (b 1 2.5) (c "hello world"))')
        assert tree[0] == "a"
        assert find_first(tree, "b") == ["b", 1, 2.5]
        assert first_atom(find_first(tree, "c")) == "hello world"

    def test_escaped_string(self):
        tree = parse_sexpr('(p "he said \\"hi\\"\\nnext")')
        assert tree[1] == 'he said "hi"\nnext'

    def test_multiline_string(self):
        tree = parse_sexpr('(p "line1\nline2")')
        assert tree[1] == "line1\nline2"

    def test_unbalanced_raises_with_location(self):
        with pytest.raises(SExprError):
            parse_sexpr("(a (b)")
        with pytest.raises(SExprError):
            parse_sexpr("(a))")

    def test_find_all(self):
        tree = parse_sexpr("(root (x 1) (y 2) (x 3))")
        assert [n[1] for n in find_all(tree, "x")] == [1, 3]


# --- schematic pin transform ---------------------------------------------------------------


class TestPinTransform:
    def _sym(self, rotation: float, mirror: str = "") -> PlacedSymbol:
        return PlacedSymbol(lib_id="Device:R", x=127.0, y=63.5, rotation=rotation, mirror=mirror)

    def test_rotation_0(self):
        # lib pin (0, 3.81) with Y-up maps below-to-above: sheet y = 63.5 - 3.81
        assert _transform_pin(0, 3.81, self._sym(0)) == pytest.approx((127.0, 59.69))

    def test_rotation_90(self):
        assert _transform_pin(0, 3.81, self._sym(90)) == pytest.approx((123.19, 63.5))

    def test_rotation_180(self):
        assert _transform_pin(0, 3.81, self._sym(180)) == pytest.approx((127.0, 67.31))

    def test_rotation_270(self):
        assert _transform_pin(0, 3.81, self._sym(270)) == pytest.approx((130.81, 63.5))


# --- schematic net building ----------------------------------------------------------------


class TestSchematic:
    @pytest.fixture(scope="class")
    def sch(self):
        text = (FIXTURES / "voltage_divider.kicad_sch").read_text()
        return parse_schematic(text, "voltage_divider.kicad_sch")

    def test_components(self, sch):
        refs = {c.reference for c in sch.components}
        assert refs == {"R1", "R2"}
        r1 = next(c for c in sch.components if c.reference == "R1")
        assert r1.value == "10k"
        assert r1.footprint == "Resistor_SMD:R_0603_1608Metric"
        assert r1.mpn == "RC0603FR-0710KL"
        assert r1.lib_id == "Device:R"

    def test_power_symbols(self, sch):
        names = {p.name for p in sch.power_symbols}
        assert names == {"+5V", "GND"}

    def test_exact_pin_net_mapping(self, sch):
        r1 = next(c for c in sch.components if c.reference == "R1")
        r2 = next(c for c in sch.components if c.reference == "R2")
        assert r1.pin("1").net == "+5V"
        assert r1.pin("2").net == "MID"
        assert r2.pin("1").net == "MID"
        assert r2.pin("2").net == "GND"

    def test_pin_types(self, sch):
        r1 = next(c for c in sch.components if c.reference == "R1")
        assert r1.pin("1").type == PinType.PASSIVE


# --- pcb -----------------------------------------------------------------------------------


class TestPcb:
    @pytest.fixture(scope="class")
    def pcb(self):
        return parse_pcb((FIXTURES / "small_board.kicad_pcb").read_text(), "small_board.kicad_pcb")

    def test_nets_and_board(self, pcb):
        assert pcb.nets[1] == "GND"
        assert pcb.board.width_mm == pytest.approx(30.0)
        assert pcb.board.height_mm == pytest.approx(20.0)
        assert {l.name for l in pcb.board.layers} >= {"F.Cu", "B.Cu", "Edge.Cuts"}

    def test_design_rules(self, pcb):
        assert pcb.board.design_rules.min_trace_width_mm == pytest.approx(0.2)
        assert pcb.board.design_rules.min_via_diameter_mm == pytest.approx(0.4)

    def test_traces_vias_zones(self, pcb):
        assert len(pcb.board.traces) == 2
        widths = sorted(t.width_mm for t in pcb.board.traces)
        assert widths == pytest.approx([0.12, 0.25])
        assert pcb.board.traces[0].net == "GND"
        assert len(pcb.board.vias) == 1
        assert pcb.board.vias[0].drill_mm == pytest.approx(0.3)
        assert len(pcb.board.zones) == 1
        assert pcb.board.zones[0].net == "GND"
        assert pcb.board.zones[0].filled is True

    def test_footprint_pads(self, pcb):
        fp = pcb.footprints[0]
        assert fp.reference == "R1"
        assert {p.net for p in fp.pads} == {"+5V", "GND"}


# --- spice / netlists ----------------------------------------------------------------------


class TestNetlists:
    def test_spice(self):
        data = parse_netlist((FIXTURES / "rc_filter.cir").read_text(), "rc_filter.cir")
        refs = {c.reference for c in data.components}
        assert refs == {"V1", "R1", "C1"}
        net_names = {n.name for n in data.nets}
        assert net_names == {"IN", "OUT", "GND"}
        gnd = next(n for n in data.nets if n.name == "GND")
        assert gnd.is_ground
        assert {(p.component, p.pin) for p in gnd.pins} == {("V1", "2"), ("C1", "2")}

    def test_kicad_netlist(self):
        text = (
            '(export (version "E")'
            ' (components (comp (ref "R1") (value "10k") (footprint "R_0603")) (comp (ref "C1") (value "100nF")))'
            ' (nets (net (code "1") (name "VCC") (node (ref "R1") (pin "1")) (node (ref "C1") (pin "1")))'
            '       (net (code "2") (name "GND") (node (ref "R1") (pin "2")) (node (ref "C1") (pin "2")))))'
        )
        data = parse_netlist(text, "project.net")
        assert data.source_format == "kicad_netlist"
        r1 = next(c for c in data.components if c.reference == "R1")
        assert r1.pin("1").net == "VCC"
        assert r1.pin("2").net == "GND"

    def test_spice_subckt_skipped(self):
        text = "X1 A B AMP\n.subckt AMP in out\nR9 in out 1k\n.ends\nR1 A 0 10k\n"
        data = parse_netlist(text, "t.cir")
        refs = {c.reference for c in data.components}
        assert "R9" not in refs
        assert {"X1", "R1"} <= refs
        assert "AMP" in data.subcircuits


# --- bom -----------------------------------------------------------------------------------


class TestBom:
    def test_flexible_headers_and_expansion(self):
        csv_text = (
            "Designator,Value,Package,Part Number\n"
            '"R1, R2",10k,0603,RC0603FR-0710KL\n'
            "R3-R5,1k,0402,RC0402\n"
        )
        rows = parse_bom_csv(csv_text)
        refs = [r.reference for r in rows]
        assert refs == ["R1", "R2", "R3", "R4", "R5"]
        assert rows[0].mpn == "RC0603FR-0710KL"
        assert rows[0].footprint == "0603"

    def test_no_reference_column(self):
        assert parse_bom_csv("A,B\n1,2\n") == []


# --- kicad_pro -----------------------------------------------------------------------------


class TestKicadPro:
    def test_design_rules(self):
        text = '{"board": {"design_settings": {"rules": {"min_track_width": 0.15, "min_clearance": 0.1}}}}'
        out = parse_project_file(text)
        assert out["design_rules"]["min_trace_width_mm"] == pytest.approx(0.15)

    def test_invalid_json(self):
        assert "error" in parse_project_file("not json")


# --- zip safety ----------------------------------------------------------------------------


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)
    return buf.getvalue()


class TestZipSafe:
    def test_traversal_rejected(self, tmp_path):
        payload = _make_zip({"../evil.kicad_sch": b"(kicad_sch)"})
        with pytest.raises(UnsafeZipError):
            safe_extract_zip(payload, tmp_path / "out")
        assert not (tmp_path / "evil.kicad_sch").exists()

    def test_absolute_path_rejected(self, tmp_path):
        payload = _make_zip({"/etc/passwd.csv": b"x"})
        with pytest.raises(UnsafeZipError):
            safe_extract_zip(payload, tmp_path / "out")

    def test_disallowed_type_skipped(self, tmp_path):
        payload = _make_zip({"run.exe": b"MZ", "ok.kicad_sch": b"(kicad_sch (version 1))"})
        paths, warnings = safe_extract_zip(payload, tmp_path / "out")
        names = [p.name for p in paths]
        assert names == ["ok.kicad_sch"]
        assert any("run.exe" in w for w in warnings)

    def test_nested_dirs_flattened(self, tmp_path):
        payload = _make_zip({"proj/sub/a.kicad_sch": b"(kicad_sch)", "b.csv": b"Reference\nR1\n"})
        paths, _ = safe_extract_zip(payload, tmp_path / "out")
        assert sorted(p.name for p in paths) == ["a.kicad_sch", "b.csv"]

    def test_too_many_files(self, tmp_path):
        payload = _make_zip({f"f{i}.csv": b"x" for i in range(20)})
        with pytest.raises(UnsafeZipError):
            safe_extract_zip(payload, tmp_path / "out", max_files=5)


# --- normalizer ----------------------------------------------------------------------------


class TestNormalizer:
    def test_schematic_project(self):
        files = [("voltage_divider.kicad_sch", (FIXTURES / "voltage_divider.kicad_sch").read_bytes())]
        project = normalize_project(files, "Divider")
        assert project.project.name == "Divider"
        assert project.project.source_format == "kicad"
        assert project.component_references() == {"R1", "R2"}
        assert project.net_names() == {"+5V", "MID", "GND"}
        plus5 = project.get_net("+5V")
        assert plus5.is_power and not plus5.is_ground
        assert plus5.inferred_voltage == pytest.approx(5.0)
        assert project.get_net("GND").is_ground

    def test_schematic_plus_pcb_merge(self):
        files = [
            ("voltage_divider.kicad_sch", (FIXTURES / "voltage_divider.kicad_sch").read_bytes()),
            ("small_board.kicad_pcb", (FIXTURES / "small_board.kicad_pcb").read_bytes()),
        ]
        project = normalize_project(files)
        assert project.board is not None
        assert project.board.width_mm == pytest.approx(30.0)
        r1 = project.get_component("R1")
        assert r1.layer == "F.Cu"
        assert r1.position.x == pytest.approx(100.0)

    def test_bom_enrichment_never_overwrites(self):
        files = [
            ("voltage_divider.kicad_sch", (FIXTURES / "voltage_divider.kicad_sch").read_bytes()),
            ("bom.csv", b'Reference,Value,MPN\nR2,999k,NEW-MPN-R2\nR1,1k,SHOULD-NOT-OVERWRITE\n'),
        ]
        project = normalize_project(files)
        r1 = project.get_component("R1")
        r2 = project.get_component("R2")
        assert r1.value == "10k"  # schematic wins
        assert r1.mpn == "RC0603FR-0710KL"  # schematic MPN preserved
        assert r2.mpn == "NEW-MPN-R2"  # gap filled from BOM
        assert r2.value == "4k7"

    def test_spice_only_project(self):
        files = [("rc_filter.cir", (FIXTURES / "rc_filter.cir").read_bytes())]
        project = normalize_project(files)
        assert project.project.source_format == "spice"
        assert project.get_net("GND").is_ground

    def test_diff_pair_detection(self):
        text = (
            '(export (version "E") (components (comp (ref "U1") (value "PHY")))'
            ' (nets (net (code "1") (name "USB_D_P") (node (ref "U1") (pin "1")))'
            '       (net (code "2") (name "USB_D_N") (node (ref "U1") (pin "2")))))'
        )
        project = normalize_project([("t.net", text.encode())])
        assert len(project.differential_pairs) == 1
        pair = project.differential_pairs[0]
        assert pair.positive_net == "USB_D_P" and pair.negative_net == "USB_D_N"

    def test_unknown_file_warning(self):
        project = normalize_project([("weird.xyz", b"data")])
        assert any("unsupported" in w.message for w in project.project.parser_warnings)
