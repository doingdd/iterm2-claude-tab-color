#!/usr/bin/env bash
# scripts/build-demo-video.sh
# HTML 视觉演示 → Chrome 单帧截图 → PNG → 6s 动画片段 (Ken Burns + 渐入)
#   → 拼接成 20s demo (1280x720 h264), 段间 0.5s cross-fade
#
# 依赖: Google Chrome.app, ffmpeg
# 用法: ./scripts/build-demo-video.sh
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
LAUNCH="$ROOT/assets/launch"
OUT="$ROOT/assets/launch/build"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

mkdir -p "$OUT"

# 帧捕获时点 (ms): 3s 后进度条/入场动画完成
SCENE_T=("scene-1-iterm:3500" "scene-2-crouter:3500" "scene-3-menubar:3000")

echo "==> [1/5] 清理残留 Chrome (避免 headless 锁冲突)"
pkill -9 -f "Google Chrome" 2>/dev/null || true
sleep 0.5

echo "==> [2/5] 3 个 HTML → 单帧 PNG (1280x720, virtual-time-budget 等动画完成)"
for spec in "${SCENE_T[@]}"; do
  name="${spec%%:*}"
  t="${spec##*:}"
  rm -f "$LAUNCH/${name}.png" "$OUT/${name}.png"
  "$CHROME" --headless --disable-gpu --no-sandbox \
    --window-size=1280,720 \
    --hide-scrollbars \
    --virtual-time-budget="$t" \
    --screenshot="$OUT/${name}.png" \
    "file://$LAUNCH/${name}.html" >/dev/null 2>&1
  echo "    - $OUT/${name}.png  (t=${t}ms)"
done

echo "==> [3/5] PNG → 6s 动画片段 (Ken Burns 1.0→1.04 + 0.4s 渐入 + 0.4s 渐出)"
cd "$OUT"
# 用临时文件记录 mp4 路径, 兼容 zsh/bash
MP_LIST="$OUT/.mp_list"
: > "$MP_LIST"
for spec in "${SCENE_T[@]}"; do
  name="${spec%%:*}"
  mp="$OUT/${name}.mp4"
  # zoompan: 6s 慢慢放大 1.0→1.04, 配合 0.3s 渐入/渐出让画面"活"起来
  # 注意: fade 滤镜的 d 单位是 秒, 不是帧
  ffmpeg -y -loglevel error \
    -loop 1 -i "$OUT/${name}.png" \
    -vf "fps=30,scale=1536:864:flags=lanczos,zoompan=z='1.0+0.04*on/180':d=180:s=1280x720:fps=30,format=yuv420p,fade=t=in:st=0:d=0.3,fade=t=out:st=5.7:d=0.3" \
    -t 6 -r 30 -c:v libx264 -preset slow -crf 23 "$mp"
  echo "$mp" >> "$MP_LIST"
  echo "    - $mp  (6s 动画)"
done

echo "==> [4/5] 拼接 3 段成 17s demo (0.5s cross-fade)"
# 读出 mp4 路径, 直接拼到 ffmpeg 命令行 (兼容 bash + zsh, 用数组 + IFS)
MPS=()
while IFS= read -r line; do
  MPS+=("$line")
done < "$MP_LIST"
ffmpeg -y -loglevel error \
  -i "${MPS[0]}" -i "${MPS[1]}" -i "${MPS[2]}" \
  -filter_complex "\
[0:v]setsar=1[v0];\
[1:v]setsar=1[v1];\
[2:v]setsar=1[v2];\
[v0][v1]xfade=transition=fade:duration=0.5:offset=5.5[v01];\
[v01][v2]xfade=transition=fade:duration=0.5:offset=11.0[outv]" \
  -map "[outv]" -r 30 -pix_fmt yuv420p -c:v libx264 -preset slow -crf 23 \
  burnkit-demo-stitched.mp4
rm -f "$MP_LIST"

echo "==> [5/5] 验证"
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,duration,r_frame_rate,nb_frames -of default burnkit-demo-stitched.mp4

echo
echo "==> 产物清单:"
ls -lh "$OUT"/*.png "$OUT"/burnkit-demo-stitched.mp4 2>/dev/null
echo
echo "✅ Done. 关键产物:"
echo "   1) $OUT/scene-1-iterm.png     (iTerm2 窗口 + 4 变色 tab + 进度条)"
echo "   2) $OUT/scene-2-crouter.png   (c router 菜单 + 4 provider + 选中态)"
echo "   3) $OUT/scene-3-menubar.png   (macOS 菜单栏 + 🔥 + 下拉面板)"
echo "   4) $OUT/burnkit-demo-stitched.mp4 (18s 拼接, h264, 段间 0.5s 软切, 可直发 X 视频)"
