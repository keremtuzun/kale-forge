# Appended verbatim to services/analysis/app/models/db.py (before init_db).
# One row per Forge studio design; the chain is the base prompt plus every
# accepted revision, so history/undo/restore need no second table.


class ForgeDesign(Base):
    __tablename__ = "forge_designs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    season: Mapped[str] = mapped_column(String(64), default="")
    prompt_chain: Mapped[str] = mapped_column(Text, default="[]")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
