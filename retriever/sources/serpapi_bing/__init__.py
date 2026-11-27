from retriever import serpapi


NAME = "SerpApi · Bing"
KEY_LABEL = "SerpApi API Key"
CONFIG_KEY = "serpapi"
configuration = serpapi.configuration


def search(home, query):
    return serpapi.search(home, "bing", query)
