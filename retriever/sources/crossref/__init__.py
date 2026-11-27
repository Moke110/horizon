"""Active Crossref search source."""
from hostrun.voyager_doi_sources import CrossrefProvider
from retriever.academic import public_configuration as configuration, search_existing

NAME = "Crossref"
KEY_LABEL = None


def search(home, query):
    return search_existing(home, CrossrefProvider, "crossref", NAME, query)
