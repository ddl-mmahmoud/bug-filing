import os
import requests
from requests.auth import HTTPBasicAuth


JIRA_BASE_URL = "https://dominodatalab.atlassian.net"
JIRA_ISSUE_URL = "https://dominodatalab.atlassian.net/rest/api/3/issue"


def jira_requests_session():
    session = requests.Session()
    auth = HTTPBasicAuth(
        os.environ["JIRA_API_USERNAME"], os.environ["JIRA_API_PASSWORD"]
    )
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    session.auth = auth
    session.headers = headers
    return session
