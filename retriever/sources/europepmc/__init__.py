"""Active Europe PMC search source."""
from hostrun.voyager_sources import EuropePMCProvider
from retriever.academic import public_configuration as configuration, search_existing

NAME = "Europe PMC"
KEY_LABEL = None


def search(home, query):
    return search_existing(home, EuropePMCProvider, "europepmc", NAME, query, native_expression=True)
