"""Active PubMed Central collection."""
from retriever import ncbi
from retriever.ncbi import configuration

NAME = "PubMed Central"
CONFIG_KEY = "ncbi"
KEY_LABEL = "NCBI API Key（可选）"


def search(home, query):
    return ncbi.search(home, "pmc", query)
