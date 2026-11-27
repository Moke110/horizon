"""Active PubMed collection."""
from retriever import ncbi
from retriever.ncbi import configuration

NAME = "PubMed"
CONFIG_KEY = "ncbi"
KEY_LABEL = "NCBI API Key（可选）"


def search(home, query):
    return ncbi.search(home, "pubmed", query)
