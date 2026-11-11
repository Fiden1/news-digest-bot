
import re

def clean_news_text(raw_text):
    """Remove polls, channel boilerplate and extra blank lines."""
    if not raw_text:
        return ""
    if "[ Poll :" in raw_text or "Ви вважаєте, що" in raw_text:
        return ""

    header_match = re.search(r"\|\s*[\w\s]+:\s*(.*)", raw_text, re.DOTALL)
    text_content = header_match.group(1) if header_match else raw_text

    garbage_patterns = [
        r"🚀Підтримати🤝_✅✅_🤝",
        r"🚀Приєднатися до ZN\.UA",
        r"Читати на русском\.",
        r"Детальніше про них читайте на нашому сайті\.",
        r"Читать на русском",
    ]
    for pattern in garbage_patterns:
        text_content = re.sub(pattern, "", text_content, flags=re.IGNORECASE)

    text_content = re.sub(r"\n{3,}", "\n\n", text_content)
    clean_text = text_content.strip()
    return clean_text if len(clean_text) > 20 else ""