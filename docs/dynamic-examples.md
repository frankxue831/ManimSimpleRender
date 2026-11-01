# Dynamic explanation examples

These are hand-authored, silent Chinese Manim scenes, not outputs of an automatic
one-prompt video generator. Their timeline JSON files select inspection frames;
the Python scenes themselves control animation and teaching pauses.

Run from the repository root in an existing Manim environment:

```sh
python -m scene_production --scene-file examples/scenes/netty_eventloop.py --scene NettyPuzzleComparison --timeline examples/inspection/netty.json --output-dir output --quality qm --timeout 300 --tool-timeout 60
python -m scene_production --scene-file examples/scenes/tcp_stream.py --scene TCPStream --timeline examples/inspection/tcp.json --output-dir output --quality qm --timeout 300 --tool-timeout 60
```

Expected: 120-second Netty and 108-second TCP videos, 1280x720 at 30 fps for `qm`.
Sources and a sibling `scene_font.py` must stay together. The scenes require a
CJK font installed in the environment. `SCENE_FONT='Noto Sans CJK SC'` selects a
specific installed family; otherwise the helper tries common CJK families then
other installed fonts. Pango checks every non-ASCII source character before
rendering. Missing families or glyphs fail explicitly. The helper uses existing
Pango libraries, and on macOS/Linux `pkg-config` when dynamic-library discovery
needs it. Font metrics and Manim versions may change layout: rerender and review.
On Windows, Pango DLL discovery must already work in the Manim environment.

## What the examples demonstrate

Start with a concrete puzzle. Preserve object identity and order while objects
move. Reveal the cause through visible state changes. Pause explicitly when
explaining a state; distinguish teaching time from model time. Use a controlled
counterfactual when comparing behavior, and state the limits of the model.
Keep captions tied to the animation rather than replacing it with slide lists.

Netty: A and B share one single-threaded NIO EventLoop. A's slow callback blocks
B's callback. In the second round only execution placement changes: slow work
moves to an available business executor, and completion posts back to the loop
before initiating a response write. Model durations are illustrative, not measured.
This is conceptual animation, not runnable Netty business code. It excludes
queue capacity, failure, timeout, cancellation, ordering and ByteBuf ownership.
Initiating a write is not proof the peer received it.

TCP: writes CAT and HELLO become the ordered bytes CATHELLO; possible reads are
CA / THEL / LO or all eight bytes. Read blocks are not network packets and no
particular grouping is promised. An illustrative application protocol prefixes
each ASCII body with a two-byte unsigned big-endian length of the BODY only.
Headers are displayed in hexadecimal. The framed stream contains 12 bytes;
read sizes are 1, 2, 6, 3. Nine buffered bytes yield A's five-byte record and leave
00 05 H E; adding L L O completes B's seven-byte record. Real implementations
must cap lengths and handle invalid input and interrupted streams. This is not
a packet capture or a TCP header format.

## Primary sources

- [Netty 4.1 EventLoop](https://netty.io/4.1/api/io/netty/channel/EventLoop.html):
  shared channel I/O execution.
- [NioEventLoop](https://netty.io/4.1/api/io/netty/channel/nio/NioEventLoop.html):
  the NIO event-loop model used here.
- [ChannelPipeline](https://netty.io/4.1/api/io/netty/channel/ChannelPipeline.html)
  and [handler execution](https://netty.io/4.1/xref/io/netty/channel/AbstractChannelHandlerContext.html):
  default execution and explicit executor assignment differ.
- [ChannelOutboundInvoker](https://netty.io/4.1/api/io/netty/channel/ChannelOutboundInvoker.html):
  asynchronous write operations.
- [RFC 9293 sections 2.2 and 3.7](https://www.rfc-editor.org/rfc/rfc9293.html):
  reliable ordered byte stream; application buffer and TCP segment boundaries
  need not correspond. The length prefix is our application-protocol example.

## Acceptance

Check full video decode and native PNG dimensions through the CLI, then inspect
native-size key frames and temporal sequences. Confirm object identity, byte
counts, transitions, labels, glyphs, overlap and readable timing. Machine media
success does not establish conceptual or visual quality. Review is sampled unless
someone actually watches the full video; report that limit accurately. Keep media,
local receipts, hashes and private QA outside Git. Change scene content by
rerendering; use `inspect` only for the unchanged recorded video.
