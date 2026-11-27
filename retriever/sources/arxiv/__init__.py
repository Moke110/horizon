"""Active arXiv search source."""
from hostrun.voyager_sources import ArxivProvider
from retriever.academic import public_configuration as configuration, search_existing

NAME = "arXiv"
KEY_LABEL = None


def search(home, query):
    return search_existing(home, ArxivProvider, "arxiv", NAME, query, native_expression=True)
