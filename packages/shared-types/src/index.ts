// Shared TypeScript types mirroring the analysis service contracts
// (services/analysis/app/models/normalized.py, ai_review.py, calculations/power_tree.py).

export type Severity = "info" | "warning" | "error" | "critical";
export type Provenance = "verified" | "user-provided" | "extracted" | "inferred" | "missing";

export interface Pin {
  number: string;
  name: string;
  type: string;
  net: string | null;
}

export interface Component {
  reference: string;
  value: string;
  footprint: string;
  mpn: string | null;
  lib_id: string;
  dnp: boolean;
  in_bom: boolean;
  position: { x: number; y: number; rotation: number } | null;
  layer: string | null;
  pins: Pin[];
  fields: Record<string, string>;
  source_file: string;
}

export interface NetPin {
  component: string;
  pin: string;
}

export interface Net {
  name: string;
  pins: NetPin[];
  is_power: boolean;
  is_ground: boolean;
  inferred_voltage: number | null;
  net_class: string | null;
}

export interface ParserWarning {
  file: string;
  message: string;
  line: number | null;
}

export interface NormalizedProject {
  project: {
    name: string;
    source_format: string;
    kicad_version: string | null;
    files: string[];
    parser_warnings: ParserWarning[];
  };
  components: Component[];
  nets: Net[];
  board: Board | null;
  mesh: Mesh | null;
  power_symbols: unknown[];
  global_labels: unknown[];
  hierarchical_labels: unknown[];
  differential_pairs: { name: string; positive_net: string; negative_net: string }[];
  unconnected_pins: NetPin[];
}

export interface Mesh {
  filename: string;
  units: string;
  vertices: number;
  texture_coordinates: number;
  normals: number;
  faces: number;
  triangles: number;
  lines: number;
  points: number;
  objects: string[];
  groups: string[];
  materials: string[];
  material_libraries: string[];
  bounds: { min_x: number; min_y: number; min_z: number; max_x: number; max_y: number; max_z: number } | null;
  boundary_edges: number;
  nonmanifold_edges: number;
  degenerate_faces: number;
  invalid_vertices: number;
  invalid_faces: number;
  topology_truncated: boolean;
}

export interface Board {
  width_mm: number | null;
  height_mm: number | null;
  layers: { name: string; kind: string }[];
  traces: unknown[];
  vias: unknown[];
  zones: unknown[];
  design_rules: Record<string, unknown>;
}

export interface Evidence {
  kind: string;
  description: string;
  reference: string | null;
}

export interface RuleFinding {
  id: string;
  rule_id: string;
  rule_version: string;
  category: string;
  severity: Severity;
  title: string;
  description: string;
  suggested_fix: string;
  confidence: number;
  source: string;
  affected_components: string[];
  affected_nets: string[];
  evidence: Evidence[];
}

export interface ReviewItem {
  title: string;
  detail: string;
  severity: Severity | null;
  components: string[];
  nets: string[];
  rule_id: string | null;
  evidence: string[];
}

export interface AIReview {
  summary: string;
  confirmed_findings: ReviewItem[];
  possible_findings: ReviewItem[];
  recommendations: ReviewItem[];
  questions_for_engineer: string[];
  evidence: Evidence[];
  confidence: number;
  limitations: string[];
}

export interface ProjectSummary {
  id: string;
  name: string;
  description: string;
  status: string;
  source_format: string;
  component_count: number;
  warning_count: number;
  error_count: number;
  critical_count: number;
  model_version: string;
  rule_engine_version: string;
  file_count: number;
  created_at: string;
  updated_at: string;
}

export interface FindingsResponse {
  rule_findings: RuleFinding[];
  ai_review: AIReview | null;
  model_version: string;
  provider: string;
  rule_engine_version: string;
}

export interface PowerCalculation {
  name: string;
  value: number | null;
  unit: string;
  formula: string;
  inputs: Record<string, number | null>;
  assumptions: string[];
  missing: string[];
  uncertainty: string;
}

export interface PowerNode {
  id: string;
  kind: "source" | "regulator" | "rail" | "load" | "ground";
  name: string;
  voltage_v: number | null;
  voltage_provenance: string;
  current_ma: number | null;
  current_provenance: string;
  component_refs: string[];
  children: string[];
}

export interface RailSummary {
  net: string;
  voltage_v: number | null;
  total_current_ma: number | null;
  load_refs: string[];
}

export interface PowerTree {
  nodes: PowerNode[];
  edges: [string, string][];
  rails: RailSummary[];
  calculations: PowerCalculation[];
  warnings: string[];
  assumptions: string[];
}

export interface ChatEvidence {
  components: string[];
  nets: string[];
  rules: string[];
  source_files: string[];
  snippets: { source: string; text: string; score: number }[];
  model_version: string;
  provider: string;
}

export interface ChatResponse {
  conversation_id: string;
  answer: string;
  evidence: ChatEvidence;
  ai_review: AIReview | null;
}

export interface ModelVersion {
  version: string;
  base_model: string;
  adapter_path: string;
  provider: string;
  status: string;
  notes: string;
  created_at: string;
}

export const SEVERITY_ORDER: Severity[] = ["critical", "error", "warning", "info"];

export const DISCLAIMER =
  "Kale Forge provides automated design-review assistance and may miss errors or produce incorrect recommendations. It is not a substitute for professional electrical engineering review, laboratory testing, simulation, regulatory certification, or manufacturer design guidance.";
