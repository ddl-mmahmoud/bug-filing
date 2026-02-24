import os
import requests
from requests.auth import HTTPBasicAuth


def jira_base_url():
    return os.environ["JIRA_URL"].rstrip('/')


def jira_requests_session():
    session = requests.Session()
    auth = HTTPBasicAuth(
        os.environ["JIRA_API_USERNAME"], os.environ["JIRA_API_PASSWORD"]
    )
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    session.auth = auth
    session.headers = headers
    return session
