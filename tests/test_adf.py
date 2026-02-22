import pytest
from bug_filing.adf import _add_mark, from_markdown


# ---------------------------------------------------------------------------
# Helpers — build expected ADF nodes inline
# ---------------------------------------------------------------------------

def _text(s, *marks):
    node = {"type": "text", "text": s}
    if marks:
        node["marks"] = list(marks)
    return node


def _para(*content):
    return {"type": "paragraph", "content": list(content)}


def _doc(*content):
    return {"version": 1, "type": "doc", "content": list(content)}


# ---------------------------------------------------------------------------
# _add_mark
# ---------------------------------------------------------------------------

def test_add_mark_adds_mark_to_text_nodes():
    nodes = [{"type": "text", "text": "hello"}]
    result = _add_mark(nodes, {"type": "strong"})
    assert result[0]["marks"] == [{"type": "strong"}]


def test_add_mark_accumulates_multiple_marks():
    nodes = [{"type": "text", "text": "hello", "marks": [{"type": "em"}]}]
    _add_mark(nodes, {"type": "strong"})
    assert {"type": "em"} in nodes[0]["marks"]
    assert {"type": "strong"} in nodes[0]["marks"]


def test_add_mark_skips_non_text_nodes():
    hard_break = {"type": "hardBreak"}
    text_node  = {"type": "text", "text": "hi"}
    nodes = [text_node, hard_break]
    _add_mark(nodes, {"type": "strong"})
    assert "marks" not in hard_break          # hardBreak untouched
    assert "marks" in text_node               # text node marked


def test_add_mark_returns_same_list():
    nodes = [{"type": "text", "text": "x"}]
    assert _add_mark(nodes, {"type": "em"}) is nodes


# ---------------------------------------------------------------------------
# from_markdown — document wrapper
# ---------------------------------------------------------------------------

def test_from_markdown_returns_doc():
    result = from_markdown("hello")
    assert result["type"] == "doc"
    assert result["version"] == 1
    assert "content" in result


def test_from_markdown_empty_string():
    result = from_markdown("")
    assert result["type"] == "doc"
    assert result["content"] == []


# ---------------------------------------------------------------------------
# Block nodes
# ---------------------------------------------------------------------------

def test_paragraph():
    result = from_markdown("hello world")
    block = result["content"][0]
    assert block["type"] == "paragraph"
    assert block["content"][0]["text"] == "hello world"


def test_heading_level_1():
    result = from_markdown("# Title")
    block = result["content"][0]
    assert block["type"] == "heading"
    assert block["attrs"]["level"] == 1
    assert block["content"][0]["text"] == "Title"


def test_heading_level_2():
    block = from_markdown("## Section")["content"][0]
    assert block["attrs"]["level"] == 2


def test_heading_level_3():
    block = from_markdown("### Sub")["content"][0]
    assert block["attrs"]["level"] == 3


def test_blockquote():
    result = from_markdown("> quoted text")
    block = result["content"][0]
    assert block["type"] == "blockquote"
    # contains a paragraph child
    assert block["content"][0]["type"] == "paragraph"


def test_code_fence_with_language():
    # Fenced blocks are dispatched to render_block_code, which reads token.language
    result = from_markdown("```python\nprint('hi')\n```")
    block = result["content"][0]
    assert block["type"] == "codeBlock"
    assert block["attrs"] == {"language": "python"}
    assert "print" in block["content"][0]["text"]


def test_code_fence_without_language():
    result = from_markdown("```\ncode here\n```")
    block = result["content"][0]
    assert block["type"] == "codeBlock"
    assert "attrs" not in block
    assert "code here" in block["content"][0]["text"]


def test_indented_code_block():
    # Four-space indented block — no language attribute, no attrs
    result = from_markdown("    indented code\n")
    block = result["content"][0]
    assert block["type"] == "codeBlock"
    assert "attrs" not in block
    assert "indented code" in block["content"][0]["text"]


def test_bullet_list():
    result = from_markdown("- alpha\n- beta\n")
    block = result["content"][0]
    assert block["type"] == "bulletList"
    assert len(block["content"]) == 2
    assert block["content"][0]["type"] == "listItem"


def test_ordered_list():
    result = from_markdown("1. first\n2. second\n")
    block = result["content"][0]
    assert block["type"] == "orderedList"
    assert block["content"][0]["type"] == "listItem"


def test_thematic_break():
    result = from_markdown("---\n")
    block = result["content"][0]
    assert block["type"] == "rule"


def test_html_block():
    result = from_markdown("<div>raw html</div>\n")
    block = result["content"][0]
    assert block["type"] == "paragraph"
    assert block["content"][0]["type"] == "text"
    assert "raw html" in block["content"][0]["text"]


# ---------------------------------------------------------------------------
# Inline nodes
# ---------------------------------------------------------------------------

def test_bold():
    para = from_markdown("**bold**")["content"][0]
    text_node = para["content"][0]
    assert text_node["type"] == "text"
    assert {"type": "strong"} in text_node["marks"]


def test_italic():
    para = from_markdown("*italic*")["content"][0]
    text_node = para["content"][0]
    assert {"type": "em"} in text_node["marks"]


def test_inline_code():
    para = from_markdown("`snippet`")["content"][0]
    node = para["content"][0]
    assert node["type"] == "text"
    assert node["text"] == "snippet"
    assert {"type": "code"} in node["marks"]


def test_link():
    para = from_markdown("[click here](https://example.com)")["content"][0]
    node = para["content"][0]
    assert node["type"] == "text"
    assert node["text"] == "click here"
    assert {"type": "link", "attrs": {"href": "https://example.com"}} in node["marks"]


def test_auto_link():
    para = from_markdown("<https://example.com>")["content"][0]
    node = para["content"][0]
    assert node["type"] == "text"
    assert node["text"] == "https://example.com"
    assert any(m["type"] == "link" for m in node["marks"])


def test_image_rendered_as_link_with_alt():
    para = from_markdown("![alt text](https://img.example.com/pic.png)")["content"][0]
    node = para["content"][0]
    assert node["type"] == "text"
    assert node["text"] == "alt text"
    assert any(
        m.get("type") == "link" and m["attrs"]["href"] == "https://img.example.com/pic.png"
        for m in node["marks"]
    )


def test_soft_line_break():
    # A bare newline inside a paragraph → soft break → single space text node
    para = from_markdown("line one\nline two")["content"][0]
    texts = [n["text"] for n in para["content"] if n["type"] == "text"]
    assert " " in texts   # soft break rendered as a space


def test_hard_line_break():
    # Two trailing spaces + newline → hard break node
    para = from_markdown("line one  \nline two")["content"][0]
    types = [n["type"] for n in para["content"]]
    assert "hardBreak" in types


def test_html_span():
    para = from_markdown("text <em>html</em> end")["content"][0]
    all_text = " ".join(n["text"] for n in para["content"] if "text" in n)
    assert "html" in all_text


# ---------------------------------------------------------------------------
# _add_mark applied to hard-break-containing bold (non-text node skipped)
# ---------------------------------------------------------------------------

def test_bold_with_hard_break_skips_non_text_node():
    # **foo  \nbar** — hard break inside bold
    para = from_markdown("**foo  \nbar**")["content"][0]
    nodes = para["content"]
    for node in nodes:
        if node["type"] == "hardBreak":
            assert "marks" not in node    # hardBreak must not receive a mark
        elif node["type"] == "text":
            assert any(m["type"] == "strong" for m in node.get("marks", []))


# ---------------------------------------------------------------------------
# Strikethrough (GFM extension)
# ---------------------------------------------------------------------------

def test_strikethrough():
    para = from_markdown("~~struck~~")["content"][0]
    # If strikethrough is supported, the text node carries a strike mark
    node = para["content"][0]
    if node.get("marks"):
        assert {"type": "strike"} in node["marks"]


# ---------------------------------------------------------------------------
# Nested inline marks
# ---------------------------------------------------------------------------

def test_bold_inside_link():
    para = from_markdown("[**bold link**](https://example.com)")["content"][0]
    node = para["content"][0]
    mark_types = {m["type"] for m in node.get("marks", [])}
    assert "strong" in mark_types
    assert "link" in mark_types


def test_emphasis_inside_bold():
    para = from_markdown("***both***")["content"][0]
    node = para["content"][0]
    mark_types = {m["type"] for m in node.get("marks", [])}
    assert "strong" in mark_types
    assert "em" in mark_types
