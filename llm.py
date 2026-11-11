import tomllib
import ollama
import json


with open("config.toml", "rb") as f:
    config = tomllib.load(f)

# The LLM must answer in this JSON shape (enforced by Ollama's structured output).
EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "importance": {"type": "integer"},
                    "analysis": {"type": "string"},
                },
                "required": ["id", "importance", "analysis"],
            },
        }
    },
    "required": ["events"],
}

def chunk_list(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def pick_events(batch, model, min_importance, think=False):
    """Ask the LLM which posts of the batch matter. Returns [(post, importance, analysis)].

    The model only returns post IDs; the text shown in the digest is always the
    original post, so the model cannot invent or alter quotes.
    """
    by_id = {post.post_id: post for post in batch}
    listing = "\n\n".join(
        f"[ID:{post.post_id} | {post.time}] {post.text[:config["start"]["MAX_CHARS_PER_POST"]]}" for post in batch
    )
    prompt = f"""You rate news posts by strategic importance.
Each post below starts with [ID:<number> | <time>].

Scale:
5 = major war development, major geopolitical decision, national-scale incident
4 = important political or economic news with national impact
3 or lower = minor local news, advertising, opinions, human-interest stories

Return only posts rated {min_importance} or higher, at most {config["start"]["MAX_EVENTS_PER_BATCH"]}.
If no post qualifies, return an empty "events" list.
For every returned post:
- "id": copy the number from the post header exactly
- "importance": your rating (integer)
- "analysis": ONE short sentence in UKRAINIAN explaining why it is strategically important

<posts>
{listing}
</posts>"""

    messages = [
        {"role": "system", "content": "You are a precise news-rating filter. Answer only with the requested JSON."},
        {"role": "user", "content": prompt},
    ]
    chat_args = dict(
        model=model,
        messages=messages,
        format=EVENT_SCHEMA,
        # with thinking on, the reasoning tokens count against num_predict, so allow much more
        options={"temperature": 0.0, "num_ctx": 8192, "num_predict": 4000 if think else 1500},
    )
    try:
        # Thinking models (gemma4, qwen3, ...) can use up the token budget on hidden
        # reasoning and return an empty answer, so thinking is off unless --think is given.
        response = ollama.chat(think=think, **chat_args)
    except TypeError:                                 # old ollama python package: no `think` option
        response = ollama.chat(**chat_args)

    content = (response["message"]["content"] or "").strip()
    if not content:
        raise ValueError("the model returned an empty answer (try --model gemma2 or update Ollama)")
    data = json.loads(content)

    events, seen = [], set()
    for item in data.get("events", []):
        post_id, importance, analysis = item.get("id"), item.get("importance"), item.get("analysis")
        # Reject hallucinated IDs, duplicates and weak events.
        if post_id not in by_id or post_id in seen:
            continue
        if not isinstance(importance, int) or importance < min_importance:
            continue
        if not isinstance(analysis, str) or not analysis.strip():
            continue
        seen.add(post_id)
        events.append((by_id[post_id], min(importance, 5), analysis.strip()))

    events.sort(key=lambda e: e[1], reverse=True)
    return events[:config["start"]["MAX_EVENTS_PER_BATCH"]]
