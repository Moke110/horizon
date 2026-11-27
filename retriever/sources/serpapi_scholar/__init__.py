"""Google Scholar search results supplied by SerpApi."""
from retriever import serpapi
from retriever.serpapi import configuration


NAME = "SerpApi · Google Scholar"
CONFIG_KEY = "serpapi"
KEY_LABEL = "SerpApi API Key"


def search(home, query):
    return serpapi.search(home, "google_scholar", query)
