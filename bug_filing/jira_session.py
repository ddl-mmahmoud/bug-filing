import os
import requests
from requests.auth import HTTPBasicAuth


def jira_base_url():
    return os.environ["JIRA_URL"].rstrip('/')


def jira_requests_session(username=None, password=None):
    session = requests.Session()
    auth = HTTPBasicAuth(
        username or os.environ["JIRA_API_USERNAME"],
        password or os.environ["JIRA_API_PASSWORD"],
    )
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    session.auth = auth
    session.headers = headers
    return session
