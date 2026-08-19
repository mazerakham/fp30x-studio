"""Local corpus of classical/ragtime themes for live piece identification.

    from fp30x_studio.idcorpus import ThemeCorpus
    tc = ThemeCorpus()
    tc.search([76, 73, 71, 70, 68, 66])

Build or rebuild the database with::

    python -m fp30x_studio.idcorpus.build

Data lives outside the repo, under ~/workspace/audio/corpus/ (see PROVENANCE.md
there for sources, licences and capture dates).
"""
from .index import Hit, ThemeCorpus, ThemeRecord
from .sources import DB_PATH, SOURCES, SOURCES_BY_ID, Source

__all__ = ["ThemeCorpus", "ThemeRecord", "Hit", "SOURCES", "SOURCES_BY_ID", "Source", "DB_PATH"]
