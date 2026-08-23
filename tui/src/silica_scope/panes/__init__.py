from __future__ import annotations


def collect_handlers(cls: type) -> type:
    # textual's metaclass builds _decorated_handlers from the class body of
    # MessagePump subclasses only, and _dispatch_method reads it off each
    # class's own __dict__ walking the MRO. a plain mixin never goes through
    # that metaclass, so its @on handlers are invisible unless we build the
    # same mapping ourselves.
    handlers: dict[object, list[tuple[object, object]]] = {}
    for value in vars(cls).values():
        for message_type, selectors in getattr(value, "_textual_on", ()):
            handlers.setdefault(message_type, []).append((value, selectors))
    cls._decorated_handlers = handlers  # type: ignore[attr-defined]
    return cls
