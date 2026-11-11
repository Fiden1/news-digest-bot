from dataclasses import dataclass, field

@dataclass
class Post:
    post_id: int
    time: str                   # "HH:MM" (UTC)
    text: str
    url: str
    image_urls: list = field(default_factory=list)   # photos and video thumbnails