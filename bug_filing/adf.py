import sys

import mistletoe
from mistletoe.base_renderer import BaseRenderer


def _add_mark(nodes, mark):
    """Add a mark to all text-type nodes in a list of inline ADF nodes."""
    for node in nodes:
        if node["type"] == "text":
            node.setdefault("marks", []).append(mark)
    return nodes


class ADFRenderer(BaseRenderer):
    """
    Renders a mistletoe document AST as an ADF (Atlassian Document Format) dict.

    Inline nodes return lists of ADF inline node dicts.
    Block nodes return a single ADF block node dict.
    """

    # ------------------------------------------------------------------ #
    # Block nodes                                                          #
    # ------------------------------------------------------------------ #

    def render_document(self, token):
        return {
            "version": 1,
            "type": "doc",
            "content": [self.render(child) for child in token.children],
        }

    def render_paragraph(self, token):
        return {"type": "paragraph", "content": self._inlines(token)}

    def render_heading(self, token):
        return {
            "type": "heading",
            "attrs": {"level": token.level},
            "content": self._inlines(token),
        }

    def render_quote(self, token):
        return {
            "type": "blockquote",
            "content": [self.render(child) for child in token.children],
        }

    def render_code_fence(self, token):
        node = {
            "type": "codeBlock",
            "content": [{"type": "text", "text": token.children[0].content}],
        }
        if token.language:
            node["attrs"] = {"language": token.language}
        return node

    def render_block_code(self, token):
        node = {
            "type": "codeBlock",
            "content": [{"type": "text", "text": token.children[0].content}],
        }
        language = getattr(token, "language", None)
        if language:
            node["attrs"] = {"language": language}
        return node

    def render_list(self, token):
        list_type = "orderedList" if token.start is not None else "bulletList"
        return {
            "type": list_type,
            "content": [self.render(child) for child in token.children],
        }

    def render_list_item(self, token):
        return {
            "type": "listItem",
            "content": [self.render(child) for child in token.children],
        }

    def render_thematic_break(self, token):
        return {"type": "rule"}

    def render_html_block(self, token):
        return {"type": "paragraph", "content": [{"type": "text", "text": token.content}]}

    # ------------------------------------------------------------------ #
    # Inline nodes (return lists of ADF inline node dicts)                #
    # ------------------------------------------------------------------ #

    def render_raw_text(self, token):
        return [{"type": "text", "text": token.content}]

    def render_strong(self, token):
        return _add_mark(self._inlines(token), {"type": "strong"})

    def render_emphasis(self, token):
        return _add_mark(self._inlines(token), {"type": "em"})

    def render_strikethrough(self, token):
        return _add_mark(self._inlines(token), {"type": "strike"})

    def render_inline_code(self, token):
        return [{"type": "text", "text": token.children[0].content, "marks": [{"type": "code"}]}]

    def render_link(self, token):
        return _add_mark(
            self._inlines(token),
            {"type": "link", "attrs": {"href": token.target}},
        )

    def render_auto_link(self, token):
        target = token.children[0].content
        return [{"type": "text", "text": target, "marks": [{"type": "link", "attrs": {"href": target}}]}]

    def render_image(self, token):
        # ADF has no inline image node; render as a link with the alt text
        alt = token.children[0].content if token.children else token.src
        return [{"type": "text", "text": alt, "marks": [{"type": "link", "attrs": {"href": token.src}}]}]

    def render_line_break(self, token):
        if token.soft:
            return [{"type": "text", "text": " "}]
        return [{"type": "hardBreak"}]

    def render_html_span(self, token):
        return [{"type": "text", "text": token.content}]

    # ------------------------------------------------------------------ #
    # Helper                                                               #
    # ------------------------------------------------------------------ #

    def _inlines(self, token):
        """Render all children and flatten any inline node lists."""
        result = []
        for child in token.children:
            rendered = self.render(child)
            if isinstance(rendered, list):
                result.extend(rendered)
            else:
                result.append(rendered)
        return result


def from_markdown(text):
    """Convert a Markdown string to an ADF document dict."""
    with ADFRenderer() as renderer:
        return renderer.render(mistletoe.Document(text))


def to_markdown(adf):
    """Convert an ADF document dict to a Markdown string."""
    return _adf_block(adf).strip()


def _adf_block(node):
    node_type = node.get("type")
    content = node.get("content", [])

    if node_type == "doc":
        parts = [_adf_block(c) for c in content]
        return "\n\n".join(p for p in parts if p)

    if node_type == "paragraph":
        return _adf_inlines(content)

    if node_type == "heading":
        level = node.get("attrs", {}).get("level", 1)
        return "#" * level + " " + _adf_inlines(content)

    if node_type == "blockquote":
        inner_parts = [_adf_block(c) for c in content]
        inner = "\n\n".join(p for p in inner_parts if p)
        return "\n".join("> " + line for line in inner.splitlines())

    if node_type == "codeBlock":
        lang = (node.get("attrs") or {}).get("language") or ""
        text = _adf_inlines(content)
        return f"```{lang}\n{text}\n```"

    if node_type == "bulletList":
        return "\n".join(_adf_list_item(c, "- ") for c in content)

    if node_type == "orderedList":
        items = [_adf_list_item(c, f"{i}. ") for i, c in enumerate(content, 1)]
        return "\n".join(items)

    if node_type == "rule":
        return "---"

    print(f"adf.to_markdown: unsupported block node {node_type!r}", file=sys.stderr)
    return ""


def _adf_list_item(node, prefix):
    """Render a listItem node with the given bullet/number prefix."""
    content = node.get("content", [])
    parts = []
    for i, child in enumerate(content):
        rendered = _adf_block(child)
        if not rendered:
            continue
        if i == 0:
            lines = rendered.splitlines()
            parts.append("\n".join(
                [prefix + lines[0]] + [" " * len(prefix) + l for l in lines[1:]]
            ))
        else:
            indent = " " * len(prefix)
            parts.append("\n".join(indent + l for l in rendered.splitlines()))
    return "\n".join(parts) if parts else prefix


def _adf_inlines(nodes):
    return "".join(_adf_inline(n) for n in nodes)


def _adf_inline(node):
    node_type = node.get("type")

    if node_type == "text":
        text = node.get("text", "")
        for mark in node.get("marks", []):
            mark_type = mark.get("type")
            if mark_type == "strong":
                text = f"**{text}**"
            elif mark_type == "em":
                text = f"*{text}*"
            elif mark_type == "strike":
                text = f"~~{text}~~"
            elif mark_type == "code":
                text = f"`{text}`"
            elif mark_type == "link":
                href = (mark.get("attrs") or {}).get("href", "")
                text = f"[{text}]({href})"
            else:
                print(f"adf.to_markdown: unsupported mark {mark_type!r}", file=sys.stderr)
        return text

    if node_type == "hardBreak":
        return "\n"

    print(f"adf.to_markdown: unsupported inline node {node_type!r}", file=sys.stderr)
    return ""
