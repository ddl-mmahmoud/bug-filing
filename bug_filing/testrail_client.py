import os
import requests
from testrail_api import TestRailAPI


TESTRAIL_URL = "https://dominodatalab.testrail.io/"

_client = None


def get_client():
    global _client
    if _client is None:
        _client = TestRailAPI(
            url=TESTRAIL_URL,
            email=os.environ["TESTRAIL_USERNAME"],
            password=os.environ["TESTRAIL_API_KEY"],
            rate_limit=True,
            retry_exceptions=[requests.exceptions.ConnectionError],
        )
    return _client


def get_test_info(test_id):
    return get_client().tests.get_test(test_id)


def get_result_info(test_id):
    return get_client().results.get_results(test_id)
