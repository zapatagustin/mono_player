"""Checks for the save-to-playlist picker: options loaded per video,
save routed with the right ids, errors degrade."""

import asyncio

from innertube import PlaylistOption
from picker import PlaylistPicker


def test_picker():
    loads, saves = [], []

    async def options_fn(client, bearer, video_id):
        loads.append(video_id)
        return [PlaylistOption("WL", "Watch later", False),
                PlaylistOption("PLx", "Mix", True)]

    async def add_fn(client, bearer, video_id, playlist_id):
        saves.append((video_id, playlist_id))
        return True

    class FakeAuth:
        async def bearer(self):
            return "tok"

    msgs = []
    m = PlaylistPicker(client=None, auth=FakeAuth(),
                       options_fn=options_fn, add_fn=add_fn)
    m.message.connect(msgs.append)

    asyncio.run(m._load("aaaaaaaaaaa"))
    assert loads == ["aaaaaaaaaaa"]
    assert m.items == [
        {"playlistId": "WL", "title": "Watch later", "contains": False},
        {"playlistId": "PLx", "title": "Mix", "contains": True},
    ]

    asyncio.run(m._save(0))
    assert saves == [("aaaaaaaaaaa", "WL")]
    assert msgs[-1] == "saved to Watch later"

    # Out-of-range save is a no-op; failing fetch degrades to empty.
    asyncio.run(m._save(9))
    assert len(saves) == 1

    async def boom(client, bearer, video_id):
        raise RuntimeError("net")

    m2 = PlaylistPicker(client=None, auth=FakeAuth(),
                        options_fn=boom, add_fn=add_fn)
    asyncio.run(m2._load("bbbbbbbbbbb"))
    assert m2.items == [] and not m2.loading
    print("playlist picker: ok")


if __name__ == "__main__":
    test_picker()
    print("all checks passed")
