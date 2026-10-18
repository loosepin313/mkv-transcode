#!/usr/bin/env bash
# Build a sample file that looks like a MakeMKV rip.
# It has an interlaced MPEG-2 video, three audio tracks in two languages,
# two subtitle tracks and two chapters.
#
# Usage: tests/make_sample.sh [output-directory]
set -euo pipefail

out="${1:-/tmp/mkv-transcode-sample}"
mkdir -p "$out"

srt_en="$out/sample-en.srt"
srt_es="$out/sample-es.srt"
chapters="$out/chapters.xml"

cat > "$srt_en" <<'EOF'
1
00:00:00,000 --> 00:00:02,000
Hello world

2
00:00:02,000 --> 00:00:04,000
Second line
EOF

cat > "$srt_es" <<'EOF'
1
00:00:00,000 --> 00:00:02,000
Hola mundo
EOF

cat > "$chapters" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<Chapters>
  <EditionEntry>
    <ChapterAtom>
      <ChapterTimeStart>00:00:00.000</ChapterTimeStart>
      <ChapterDisplay><ChapString>Chapter 1</ChapString><ChapCountry>us</ChapCountry></ChapterDisplay>
    </ChapterAtom>
    <ChapterAtom>
      <ChapterTimeStart>00:00:04.000</ChapterTimeStart>
      <ChapterDisplay><ChapString>Chapter 2</ChapString><ChapCountry>us</ChapCountry></ChapterDisplay>
    </ChapterAtom>
  </EditionEntry>
</Chapters>
EOF

target="$out/Alien.1979.1080p.BluRay.DTS-HD.MA.5.1.mkv"

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=1280x720:rate=24:duration=8" \
  -f lavfi -i "sine=frequency=440:duration=8" \
  -f lavfi -i "sine=frequency=880:duration=8" \
  -f lavfi -i "sine=frequency=220:duration=8" \
  -i "$srt_en" \
  -i "$srt_es" \
  -map 0:v -c:v mpeg2video -q:v 8 \
  -map 1:a -c:a:0 ac3 -b:a:0 448k -channel_layout 5.1 -metadata:s:a:0 language=eng \
  -map 2:a -c:a:1 aac -b:a:1 160k -metadata:s:a:1 language=spa \
  -map 3:a -c:a:2 pcm_s16le -metadata:s:a:2 language=eng -metadata:s:a:2 title="Commentary" \
  -map 4:s -c:s:0 srt -metadata:s:s:0 language=eng \
  -map 5:s -c:s:1 srt -metadata:s:s:1 language=spa \
  -metadata title="Alien (1979)" \
  "$target"

if command -v mkvmerge >/dev/null 2>&1; then
  mkvmerge -o "$out/tmp-with-chapters.mkv" --chapters "$chapters" "$target" \
    && mv "$out/tmp-with-chapters.mkv" "$target"
fi

echo "wrote $target"
ffprobe -v error -show_entries stream=index,codec_type,codec_name:stream_tags=language,title \
  -of default=noprint_wrappers=1 "$target"
