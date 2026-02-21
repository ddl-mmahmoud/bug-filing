import re
import html


def generate_jira_message_with_links(original_text: str):
    """
    Formats a TestRail result comment into Jira Atlassian Document Format (ADF),
    converting Markdown-style links into clickable Jira link nodes.
    """
    link_pattern = r"\[([^\]]+)\]\(([^)]+)\)"
    links = re.findall(link_pattern, original_text)
    content = []

    failure_message = original_text.split("\n\n")[0]
    failure_message = html.unescape(failure_message)
    content.append(
        {"type": "paragraph", "content": [{"type": "text", "text": failure_message}]}
    )

    for link_text, link_url in links:
        paragraph = {
            "type": "paragraph",
            "content": [
                {
                    "type": "text",
                    "text": link_text,
                    "marks": [{"type": "link", "attrs": {"href": link_url}}],
                }
            ],
        }
        content.append(paragraph)

    return {"content": content}
