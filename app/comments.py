"""Comments for the watch panel: loaded on demand when the panel opens
(comments are heavy and not always wanted), LRU-cached per video id, with
next-page continuation and inline reply expansion."""

import asyncio
from collections import OrderedDict

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QModelIndex,
    Qt,
    Signal,
    Slot,
)

import innertube

_ROLE_KEYS = ("author", "text", "likes", "published", "replies", "depth",
              "hasReplies", "expanded", "avatar", "liked")
_ROLES = {Qt.ItemDataRole.UserRole + 1 + i: key
          for i, key in enumerate(_ROLE_KEYS)}


def _to_item(comment, depth: int) -> dict:
    return {
        "author": comment.author, "text": comment.text,
        "likes": comment.likes, "published": comment.published,
        "replies": comment.replies, "depth": depth,
        "replyToken": comment.reply_token,
        "expanded": False,
        "avatar": comment.avatar_url,
        "likeAction": comment.like_action,
        "unlikeAction": comment.unlike_action,
        "liked": comment.liked,
        "id": comment.comment_id,
        "hasReplies": comment.reply_token != "" or (
            depth == 0 and comment.replies != ""),
    }


class CommentsModel(QAbstractListModel):
    """Backing the panel ListView: mutations are GRANULAR (insert/remove/
    dataChanged) — a wholesale reset would drop the view's scroll position.
    Only a new video resets."""

    changed = Signal()  # loading/hasMore status
    message = Signal(str)

    def __init__(self, client, auth=None, fetch_fn=None, page_fn=None,
                 cache_size=16, parent=None):
        super().__init__(parent)
        self._client = client
        self._auth = auth
        self._fetch = fetch_fn or innertube.comments
        self._page = page_fn or innertube.comments_page
        self._action_fn = innertube.comment_action
        self._cache: OrderedDict[str, tuple] = OrderedDict()
        self._reply_cache: dict[str, list] = {}
        self._cache_size = cache_size
        self._video_id = ""
        self._web_tokens_for: set[str] = set()
        self._items: list[dict] = []
        self._next_token = ""
        self._loading = False

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._items)

    def roleNames(self):
        return {role: key.encode() for role, key in _ROLES.items()}

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        key = _ROLES.get(role)
        return self._items[index.row()].get(key) if key else None

    def _row_changed(self, row: int):
        idx = self.index(row)
        self.dataChanged.emit(idx, idx)

    @Slot(str)
    def setCurrent(self, video_id: str):
        """Track the active video; never fetches — load is on demand."""
        if video_id == self._video_id:
            return
        self._video_id = video_id
        if self._items or self._next_token:
            self.beginResetModel()
            self._items = []
            self.endResetModel()
            self._next_token = ""
            self.changed.emit()

    @Slot()
    def loadCurrent(self):
        asyncio.create_task(self._load())

    @Slot()
    def loadMore(self):
        asyncio.create_task(self._load_more())

    @Slot(int)
    def toggleReplies(self, index: int):
        asyncio.create_task(self._toggle(index))

    @Slot(int)
    def likeComment(self, index: int):
        asyncio.create_task(self._like(index))

    async def _like(self, index: int):
        if not 0 <= index < len(self._items):
            return
        item = self._items[index]
        unliking = item["liked"] and item["unlikeAction"] != ""
        action = item["unlikeAction"] if unliking else item["likeAction"]
        if not action:
            # anonymous fetches carry no action params
            self.message.emit("like unavailable (login?)")
            return
        bearer = await self._auth.bearer() if self._auth is not None else None
        if bearer is None:
            self.message.emit("login required (gl)")
            return
        try:
            await self._action_fn(self._client, bearer, action)
            item["liked"] = not unliking
            self._row_changed(index)
            self.message.emit("comment unliked" if unliking
                              else "comment liked")
        except Exception as exc:
            print(f"comments: like failed: {exc!r}")
            self.message.emit("comment like failed")

    async def _load(self):
        video_id = self._video_id
        if not video_id or self._loading:
            return
        if video_id in self._cache:
            self._cache.move_to_end(video_id)
            comments, token = self._cache[video_id]
            self._apply(video_id, comments, token)
            return
        self._set_loading(True)
        bearer = await self._auth.bearer() if self._auth is not None else None
        try:
            comments, token = await self._fetch(self._client, video_id, bearer)
        except Exception as exc:
            print(f"comments: fetch failed: {exc!r}")
            comments, token = [], ""
        self._remember(video_id, comments, token)
        self._apply(video_id, comments, token)

    async def _load_more(self):
        video_id = self._video_id
        if not self._next_token or self._loading:
            return
        self._set_loading(True)
        bearer = await self._auth.bearer() if self._auth is not None else None
        try:
            more, token = await self._page(self._client, self._next_token,
                                           bearer)
        except Exception as exc:
            print(f"comments: page failed: {exc!r}")
            more, token = [], ""
        self._set_loading(False)
        if video_id != self._video_id:
            return
        if more:
            first = len(self._items)
            self.beginInsertRows(QModelIndex(), first, first + len(more) - 1)
            self._items.extend(_to_item(c, 0) for c in more)
            self.endInsertRows()
        self._next_token = token
        cached = self._cache.get(video_id)
        if cached is not None:
            self._remember(video_id, cached[0] + list(more), token)
        self.changed.emit()

    async def _toggle(self, index: int):
        if not 0 <= index < len(self._items):
            return
        item = self._items[index]
        if item["depth"] != 0 or not item["hasReplies"]:
            return
        if not item["replyToken"]:
            # Authenticated ANDROID listings carry no reply tokens; fetch
            # the anonymous WEB listing once and map them by comment id.
            await self._fill_reply_tokens()
            if not item["replyToken"]:
                self.message.emit("replies unavailable")
                return
        if item["expanded"]:
            end = index + 1
            while end < len(self._items) and self._items[end]["depth"] == 1:
                end += 1
            if end > index + 1:
                self.beginRemoveRows(QModelIndex(), index + 1, end - 1)
                del self._items[index + 1:end]
                self.endRemoveRows()
            item["expanded"] = False
            self._row_changed(index)
            return
        token = item["replyToken"]
        replies = self._reply_cache.get(token)
        if replies is None:
            video_id = self._video_id
            self._set_loading(True)
            bearer = (await self._auth.bearer()
                      if self._auth is not None else None)
            try:
                replies, _ = await self._page(self._client, token, bearer)
            except Exception as exc:
                print(f"comments: replies failed: {exc!r}")
                replies = []
            self._reply_cache[token] = replies
            self._set_loading(False)
            if video_id != self._video_id:
                return
        if replies:
            self.beginInsertRows(QModelIndex(), index + 1,
                                 index + len(replies))
            self._items[index + 1:index + 1] = [_to_item(c, 1)
                                                for c in replies]
            self.endInsertRows()
        item["expanded"] = True
        self._row_changed(index)

    async def _fill_reply_tokens(self):
        video_id = self._video_id
        if video_id in self._web_tokens_for:
            return  # already fetched for this video; unmatched stay empty
        try:
            web_comments, _ = await self._fetch(self._client, video_id, None)
        except Exception as exc:
            print(f"comments: web token fetch failed: {exc!r}")
            return
        if video_id != self._video_id:
            return
        self._web_tokens_for.add(video_id)
        tokens = {c.comment_id: c.reply_token
                  for c in web_comments if c.reply_token}
        for item in self._items:
            token = tokens.get(item["id"], "")
            if token and not item["replyToken"]:
                item["replyToken"] = token  # internal, not a visible role

    def _remember(self, video_id: str, comments, token: str):
        self._cache[video_id] = (comments, token)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _apply(self, video_id: str, comments, token: str):
        self._loading = False
        if video_id != self._video_id:
            self.changed.emit()
            return  # video changed while fetching
        self.beginResetModel()  # fresh listing: top-of-list is correct here
        self._items = [_to_item(c, 0) for c in comments]
        self.endResetModel()
        self._next_token = token
        self.changed.emit()

    def _set_loading(self, value: bool):
        self._loading = value
        self.changed.emit()

    items = Property("QVariantList", lambda s: s._items, notify=changed)
    loading = Property(bool, lambda s: s._loading, notify=changed)
    hasMore = Property(bool, lambda s: s._next_token != "", notify=changed)
