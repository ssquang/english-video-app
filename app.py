import os
import json
import re
import random
import urllib.parse
import urllib.request
import threading
import subprocess
import time
import wave
import requests
import flet as ft

# ================= NẠP ENGINE MOONSHINE VOICE (NẾU CÓ) =================
HAVE_MOONSHINE_API = False
try:
    from moonshine_voice import (
        Transcriber as MV_Transcriber,
        TranscriptEventListener as MV_TranscriptEventListener,
        get_model_for_language as mv_get_model_for_language,
        load_wav_file as mv_load_wav_file,
    )
    HAVE_MOONSHINE_API = True
except Exception:
    try:
        import moonshine
    except Exception:
        moonshine = None

# ================= THUẬT TOÁN ĐỐI CHIẾU TỪ ĐỒNG NGHĨA =================
DEFAULT_SAMPLE_WORDS = [
    {"en": "conversation", "vi": "cuộc trò chuyện, đàm thoại, đối thoại", "hint": "c...n"},
    {"en": "express", "vi": "bày tỏ, biểu lộ, diễn đạt", "hint": "e...s"},
    {"en": "favorite", "vi": "yêu thích, ưa chuộng", "hint": "f...e"},
    {"en": "interview", "vi": "phỏng vấn, cuộc gặp mặt", "hint": "i...w"},
    {"en": "confidence", "vi": "sự tự tin, lòng tin, sự tin tưởng", "hint": "c...e"}
]

def clean_text(s: str) -> str:
    if not s:
        return ""
    s_cleaned = re.sub(r'[^\w\s]', ' ', s)
    return re.sub(r'\s+', ' ', s_cleaned).strip().lower()

def extract_meanings(raw_str: str) -> list:
    if not raw_str:
        return []
    clean_paren = re.sub(r'\(.*?\)', '', raw_str)
    raw_candidates = re.split(r'[,;/|\n\+]', raw_str) + re.split(r'[,;/|\n\+]', clean_paren)
    results = set()
    for item in raw_candidates:
        c = clean_text(item)
        if c:
            results.add(c)
    return list(results)

def check_en_to_vi_smart(user_vi: str, target_en: str, target_vi_raw: str, offline_dict: dict) -> bool:
    u_clean = clean_text(user_vi)
    if not u_clean:
        return False
    acceptable = extract_meanings(target_vi_raw)
    if u_clean in acceptable or any(u_clean in m or m in u_clean for m in acceptable if len(u_clean) >= 2):
        return True
    offline_mean = offline_dict.get(clean_text(target_en), "")
    if offline_mean:
        off_meanings = extract_meanings(offline_mean)
        if u_clean in off_meanings or any(u_clean in m or m in u_clean for m in off_meanings if len(u_clean) >= 2):
            return True
    try:
        url = f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(u_clean)}&langpair=vi|en"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            translations = [clean_text(data.get("responseData", {}).get("translatedText", ""))]
            for m in data.get("matches", []):
                translations.append(clean_text(m.get("translation", "")))
            target_clean = clean_text(target_en)
            if any(target_clean == t or target_clean in t.split() for t in translations):
                return True
    except Exception:
        pass
    return False

def check_vi_to_en_smart(user_en: str, target_vi_raw: str, original_en: str, current_words: list, offline_dict: dict) -> bool:
    u_en_clean = clean_text(user_en)
    if not u_en_clean:
        return False
    if u_en_clean == clean_text(original_en):
        return True
    target_vi_meanings = extract_meanings(target_vi_raw)
    for w in current_words:
        w_meanings = extract_meanings(w.get("vi", ""))
        if any(m in target_vi_meanings for m in w_meanings):
            if u_en_clean == clean_text(w.get("en", "")):
                return True
    user_en_vi = offline_dict.get(u_en_clean, "")
    if user_en_vi:
        user_meanings = extract_meanings(user_en_vi)
        if any(m in target_vi_meanings or any(m in t or t in m for t in target_vi_meanings) for m in user_meanings if len(m) >= 2):
            return True
    try:
        url = f"https://api.mymemory.translated.net/get?q={urllib.parse.quote(u_en_clean)}&langpair=en|vi"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            translated_vi = [clean_text(data.get("responseData", {}).get("translatedText", ""))]
            for m in data.get("matches", []):
                translated_vi.extend(extract_meanings(m.get("translation", "")))
            if any(t in target_vi_meanings or any(t in m or m in t for m in target_vi_meanings) for t in translated_vi if len(t) >= 2):
                return True
    except Exception:
        pass
    return False

# ================= NHẬN DIỆN MOONSHINE TRÊN MÁY TÍNH =================
if HAVE_MOONSHINE_API:
    class SubtitleCollector(MV_TranscriptEventListener):
        def __init__(self):
            super().__init__()
            self.lines = {}

        def _record(self, event):
            line = getattr(event, "line", None)
            if not line:
                return
            line_id = getattr(line, "id", None) or getattr(line, "line_id", None) or len(self.lines)
            txt = getattr(line, "text", "").strip()
            start = float(getattr(line, "start_time", 0.0))
            dur = float(getattr(line, "duration", 0.0))
            if txt:
                self.lines[line_id] = {
                    "start": round(start, 2),
                    "end": round(start + (dur if dur > 0 else 3.5), 2),
                    "text": txt
                }

        def on_line_started(self, event): self._record(event)
        def on_line_text_changed(self, event): self._record(event)
        def on_line_updated(self, event): self._record(event)
        def on_line_completed(self, event): self._record(event)

        def get_segments(self):
            return sorted(list(self.lines.values()), key=lambda x: x["start"])

def transcribe_local_moonshine(video_path: str, temp_dir: str, progress_callback=None) -> tuple:
    if not HAVE_MOONSHINE_API:
        raise RuntimeError("Mô hình AI chỉ chạy trên máy tính.")

    wav_temp = os.path.join(temp_dir, "temp_audio_16k.wav")
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        wav_temp
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except FileNotFoundError:
        raise RuntimeError("Máy tính chưa cài FFmpeg hoặc chưa thêm FFmpeg vào PATH.")

    segments = []
    total_duration = 0.0
    try:
        with wave.open(wav_temp, "rb") as wf:
            total_duration = round(wf.getnframes() / float(wf.getframerate()), 2)

        model_path, model_arch = mv_get_model_for_language("en")
        transcriber = MV_Transcriber(model_path=model_path, model_arch=model_arch)
        collector = SubtitleCollector()
        transcriber.add_listener(collector)

        audio_data, sample_rate = mv_load_wav_file(wav_temp)
        transcriber.start()

        chunk_duration = 0.5
        chunk_size = int(chunk_duration * sample_rate)
        total = len(audio_data)

        for i in range(0, total, chunk_size):
            chunk = audio_data[i : i + chunk_size]
            transcriber.add_audio(chunk, sample_rate)
            if progress_callback and total > 0:
                progress_callback(int((i + len(chunk)) / total * 100))

        transcriber.stop()
        segments = collector.get_segments()
    finally:
        if os.path.exists(wav_temp):
            try:
                os.remove(wav_temp)
            except Exception:
                pass
    return segments, total_duration

# ================= GIAO DIỆN ỨNG DỤNG =================
def main(page: ft.Page):
    page.title = "Học Tiếng Anh - Moonshine Offline"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 0
    page.window.width = 440
    page.window.height = 860

    app_data_dir = os.path.join(os.path.expanduser("~"), ".english_video_app")
    os.makedirs(app_data_dir, exist_ok=True)
    dict_path = os.path.join(app_data_dir, "offline_dict.json")

    offline_dict = {}
    if os.path.exists(dict_path):
        try:
            with open(dict_path, "r", encoding="utf-8") as f:
                offline_dict = json.load(f)
        except Exception:
            offline_dict = {}
    if not offline_dict:
        offline_dict = {"hello": "xin chào", "video": "đoạn phim", "learn": "học tập", "practice": "thực hành"}

    def save_offline_dict():
        try:
            with open(dict_path, "w", encoding="utf-8") as f:
                json.dump(offline_dict, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    tts_audio = ft.Audio(
        src="https://translate.google.com/translate_tts?ie=UTF-8&tl=en&client=tw-ob&q=hello",
        autoplay=False
    )
    page.overlay.append(tts_audio)

    def speak(word: str):
        clean_w = re.sub(r'[^a-zA-Z]', '', word)
        if clean_w:
            try:
                tts_audio.src = f"https://translate.google.com/translate_tts?ie=UTF-8&tl=en&client=tw-ob&q={urllib.parse.quote(clean_w)}"
                tts_audio.update()
                tts_audio.play()
            except Exception:
                pass

    state = {
        "current_video_name": "",
        "current_video_path": "",
        "subtitles": [],
        "vocab_list": [],
        "active_sub_idx": -1,
        "current_pos_sec": 0.0,
        "duration_sec": 0.0,
        "is_playing": False,
        "is_fullscreen": False,
        "active_video_ref": None,
        "q_en_idx": 0,
        "q_vi_idx": 0
    }

    def clean_filename(name: str) -> str:
        name = re.sub(r'[\\/*?:"<>|]', "", name)
        return re.sub(r'\s+', "_", name.strip())[:50]

    def get_vocab_path(v_name: str) -> str:
        return os.path.join(app_data_dir, f"{clean_filename(v_name)}_vocab.json")

    def get_subs_path(v_name: str) -> str:
        return os.path.join(app_data_dir, f"{clean_filename(v_name)}_subs.json")

    def load_vocab(v_name: str):
        p = get_vocab_path(v_name)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        words = list(DEFAULT_SAMPLE_WORDS)
        save_vocab(v_name, words)
        return words

    def save_vocab(v_name: str, words: list):
        try:
            with open(get_vocab_path(v_name), "w", encoding="utf-8") as f:
                json.dump(words, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def add_vocab(word: str, meaning: str):
        word, meaning = word.strip(), meaning.strip()
        if not word or not meaning or not state["current_video_name"]:
            return
        words = state["vocab_list"]
        if not any(item["en"].lower() == word.lower() for item in words):
            hint = f"{word[0]}...{word[-1]}" if len(word) > 2 else word
            words.append({"en": word, "vi": meaning, "hint": hint})
            save_vocab(state["current_video_name"], words)
            offline_dict[word.lower()] = meaning
            save_offline_dict()
            page.snack_bar = ft.SnackBar(ft.Text(f"Đã lưu: '{word}'!"), bgcolor=ft.Colors.GREEN_700)
            page.snack_bar.open = True
            refresh_vocab_ui()
            page.update()

    # ================= DIALOG TRA TỪ =================
    def open_lookup_dialog(word: str):
        target = re.sub(r'[^a-zA-Z]', '', word).lower()
        if not target:
            return
        speak(target)

        word_title = ft.Text(target.capitalize(), size=22, weight=ft.FontWeight.BOLD, color=ft.Colors.AMBER)
        meaning_text = ft.Text("⏳ Đang tra nghĩa...", size=16, color=ft.Colors.WHITE)
        source_badge = ft.Container(
            content=ft.Text("Online", size=11, weight=ft.FontWeight.BOLD),
            padding=ft.padding.symmetric(horizontal=6, vertical=2),
            bgcolor=ft.Colors.BLUE_700,
            border_radius=4
        )

        def save_and_close(e):
            add_vocab(target, meaning_text.value)
            dialog.open = False
            page.update()

        btn_save = ft.ElevatedButton("➕ Lưu từ vào bài học", on_click=save_and_close, visible=False)

        dialog = ft.AlertDialog(
            title=ft.Row([
                ft.Row([word_title, source_badge], spacing=8),
                ft.IconButton(ft.Icons.VOLUME_UP, on_click=lambda _: speak(target), icon_color=ft.Colors.LIGHT_BLUE)
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            content=ft.Column([
                meaning_text,
                ft.Container(height=8),
                btn_save
            ], tight=True, spacing=6),
            actions=[
                ft.TextButton("Đóng", on_click=lambda _: setattr(dialog, "open", False) or page.update())
            ]
        )
        page.dialog = dialog
        dialog.open = True
        page.update()

        def fetch_meaning():
            if target in offline_dict:
                meaning = offline_dict[target]
                source_badge.content.value = "Offline"
                source_badge.bgcolor = ft.Colors.GREEN_700
            else:
                try:
                    res = requests.get(f"https://api.mymemory.translated.net/get?q={target}&langpair=en|vi", timeout=3).json()
                    meaning = res.get("responseData", {}).get("translatedText", "Không tìm thấy nghĩa.")
                except Exception:
                    meaning = "Lỗi kết nối mạng khi tra từ."

            meaning_text.value = meaning
            btn_save.visible = bool(meaning and "lỗi" not in meaning.lower())
            page.update()

        threading.Thread(target=fetch_meaning, daemon=True).start()

    # ================= KHUNG VIDEO & PHỤ ĐỀ TRÊN VIDEO =================
    sub_chips_row = ft.Row(
        wrap=True,
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=6,
        run_spacing=4
    )
    sub_time_badge = ft.Text("[00:00]", size=12, color=ft.Colors.AMBER_300, weight=ft.FontWeight.BOLD)

    overlay_sub_box = ft.Container(
        content=ft.Column(
            controls=[
                ft.Row([sub_time_badge, ft.Text("Chạm chữ để tra từ", size=11, color=ft.Colors.GREY_300)], alignment=ft.MainAxisAlignment.CENTER),
                sub_chips_row
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
            spacing=3
        ),
        bgcolor=ft.Colors.BLACK87,
        border_radius=10,
        padding=ft.padding.symmetric(horizontal=12, vertical=6),
        alignment=ft.alignment.center,
        border=ft.border.all(1.5, ft.Colors.AMBER_400),
        visible=True
    )

    status_label = ft.Text("Chưa chọn video", size=13, color=ft.Colors.GREY_400, text_align=ft.TextAlign.CENTER)
    loading_ring = ft.ProgressRing(visible=False, width=20, height=20, stroke_width=2)
    transcript_col = ft.Column(scroll=ft.ScrollMode.AUTO, spacing=6)

    def render_subtitle_chips(text: str, time_str: str = ""):
        sub_chips_row.controls.clear()
        if time_str:
            sub_time_badge.value = time_str
        for w in text.split():
            clean_w = re.sub(r'[^a-zA-Z0-9]', '', w)
            if not clean_w:
                continue
            btn = ft.Container(
                content=ft.Text(
                    w,
                    size=20 if state["is_fullscreen"] else 16,
                    weight=ft.FontWeight.BOLD,
                    color=ft.Colors.WHITE
                ),
                padding=ft.padding.symmetric(horizontal=8, vertical=4),
                bgcolor=ft.Colors.BLUE_GREY_900,
                border=ft.border.all(1, ft.Colors.AMBER_300),
                border_radius=6,
                ink=True,
                on_click=lambda e, target=clean_w: open_lookup_dialog(target)
            )
            sub_chips_row.controls.append(btn)
        page.update()

    def highlight_transcript(active_idx: int):
        for idx, ctrl in enumerate(transcript_col.controls):
            if isinstance(ctrl, ft.Container):
                is_active = (idx == active_idx)
                ctrl.bgcolor = ft.Colors.BLUE_900 if is_active else ft.Colors.WHITE10
        page.update()

    def get_best_subtitle_index(cur_sec: float) -> int:
        subs = state["subtitles"]
        if not subs:
            return -1
        for idx, seg in enumerate(subs):
            if seg["start"] <= cur_sec <= seg["end"] + 0.6:
                return idx
        for idx in range(len(subs) - 1, -1, -1):
            if cur_sec >= subs[idx]["start"]:
                return idx
        return 0

    def check_and_update_subtitle(cur_sec: float, force: bool = False):
        subs = state["subtitles"]
        if not subs:
            return

        target_idx = get_best_subtitle_index(cur_sec)
        if target_idx != -1 and (force or state["active_sub_idx"] != target_idx):
            state["active_sub_idx"] = target_idx
            m, s = int(subs[target_idx]["start"] // 60), int(subs[target_idx]["start"] % 60)
            render_subtitle_chips(subs[target_idx]["text"], f"[{m:02d}:{s:02d}]")
            highlight_transcript(target_idx)

    time_display = ft.Text("00:00 / 00:00", size=12, color=ft.Colors.GREY_300)
    time_slider = ft.Slider(min=0, max=100, value=0, expand=True)

    def on_slider_change(e):
        seek_to_sec(float(e.control.value))

    time_slider.on_change = on_slider_change

    def seek_to_sec(sec: float):
        sec = max(0.0, min(sec, state["duration_sec"] if state["duration_sec"] > 0 else 9999.0))
        state["current_pos_sec"] = sec
        ms = int(sec * 1000)
        vp = state["active_video_ref"]
        if vp:
            try:
                if hasattr(vp, "seek"):
                    vp.seek(ms)
                elif hasattr(vp, "seek_position"):
                    vp.seek_position(ms)
            except Exception:
                pass
        update_time_label()
        check_and_update_subtitle(sec, force=True)

    def update_time_label():
        c_m, c_s = int(state["current_pos_sec"] // 60), int(state["current_pos_sec"] % 60)
        d_m, d_s = int(state["duration_sec"] // 60), int(state["duration_sec"] % 60)
        time_display.value = f"{c_m:02d}:{c_s:02d} / {d_m:02d}:{d_s:02d}"
        if state["duration_sec"] > 0:
            time_slider.value = min(state["current_pos_sec"], state["duration_sec"])
        page.update()

    def toggle_play(e=None):
        vp = state["active_video_ref"]
        if not vp:
            return
        try:
            if state["is_playing"]:
                vp.pause()
                play_btn.icon = ft.Icons.PLAY_ARROW
                state["is_playing"] = False
            else:
                vp.play()
                play_btn.icon = ft.Icons.PAUSE
                state["is_playing"] = True
            page.update()
        except Exception:
            pass

    play_btn = ft.IconButton(ft.Icons.PAUSE, icon_size=30, icon_color=ft.Colors.AMBER, on_click=toggle_play)

    def jump_sentence(delta: int):
        subs = state["subtitles"]
        if not subs:
            return
        new_idx = max(0, min(len(subs) - 1, state["active_sub_idx"] + delta))
        seek_to_sec(subs[new_idx]["start"])

    def toggle_fullscreen(e):
        state["is_fullscreen"] = not state["is_fullscreen"]
        if state["is_fullscreen"]:
            video_container.height = None
            video_container.expand = True
            btn_fs.icon = ft.Icons.FULLSCREEN_EXIT
            page.navigation_bar.visible = False
            transcript_section.visible = False
            top_bar.visible = False
        else:
            video_container.height = 240
            video_container.expand = False
            btn_fs.icon = ft.Icons.FULLSCREEN
            page.navigation_bar.visible = True
            transcript_section.visible = True
            top_bar.visible = True

        if 0 <= state["active_sub_idx"] < len(state["subtitles"]):
            cur = state["subtitles"][state["active_sub_idx"]]
            render_subtitle_chips(cur["text"])
        page.update()

    btn_fs = ft.IconButton(
        icon=ft.Icons.FULLSCREEN,
        icon_color=ft.Colors.WHITE,
        bgcolor=ft.Colors.BLACK54,
        on_click=toggle_fullscreen
    )

    video_container = ft.Container(
        content=ft.Text("Chưa chọn video", color=ft.Colors.GREY_500),
        height=240,
        bgcolor=ft.Colors.BLACK,
        border_radius=8,
        alignment=ft.alignment.center
    )

    controls_bar = ft.Column([
        ft.Row([time_slider, time_display], spacing=8),
        ft.Row(
            controls=[
                ft.TextButton("⏮ Câu trước", on_click=lambda _: jump_sentence(-1)),
                ft.IconButton(ft.Icons.REPLAY_5, on_click=lambda _: seek_to_sec(state["current_pos_sec"] - 5)),
                play_btn,
                ft.IconButton(ft.Icons.FORWARD_5, on_click=lambda _: seek_to_sec(state["current_pos_sec"] + 5)),
                ft.TextButton("Câu sau ⏭", on_click=lambda _: jump_sentence(1)),
            ],
            alignment=ft.MainAxisAlignment.CENTER
        )
    ], spacing=0)

    def playback_ticker():
        while True:
            time.sleep(0.25)
            if state["is_playing"] and state["active_video_ref"]:
                state["current_pos_sec"] += 0.25
                if state["duration_sec"] > 0 and state["current_pos_sec"] >= state["duration_sec"]:
                    state["current_pos_sec"] = state["duration_sec"]
                    state["is_playing"] = False
                    play_btn.icon = ft.Icons.PLAY_ARROW

                update_time_label()
                check_and_update_subtitle(state["current_pos_sec"])

    threading.Thread(target=playback_ticker, daemon=True).start()

    def build_transcript_list():
        transcript_col.controls.clear()
        if not state["subtitles"]:
            transcript_col.controls.append(ft.Text("Chưa có phụ đề.", color=ft.Colors.GREY_500))
            page.update()
            return

        for idx, seg in enumerate(state["subtitles"]):
            start_sec = float(seg.get("start", 0.0))
            m, s = int(start_sec // 60), int(start_sec % 60)
            t_badge = f"[{m:02d}:{s:02d}]"
            text_str = str(seg.get("text", "")).strip()

            def make_click(s_time=start_sec):
                return lambda _: (
                    seek_to_sec(s_time),
                    check_and_update_subtitle(s_time, force=True)
                )

            item = ft.Container(
                content=ft.Row([
                    ft.Text(t_badge, size=13, color=ft.Colors.LIGHT_BLUE_ACCENT, weight=ft.FontWeight.BOLD),
                    ft.Text(text_str, size=15, color=ft.Colors.WHITE, expand=True)
                ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.padding.symmetric(horizontal=10, vertical=8),
                border_radius=8,
                bgcolor=ft.Colors.WHITE10,
                ink=True,
                on_click=make_click(start_sec)
            )
            transcript_col.controls.append(item)
        page.update()

    def process_subtitles_moonshine(video_path: str, v_name: str):
        loading_ring.visible = True
        status_label.value = "🌙 Moonshine đang nhận diện offline..."
        page.update()

        def on_prog(percent):
            status_label.value = f"🌙 Moonshine Offline: {percent}%"
            page.update()

        try:
            subs_data, dur = transcribe_local_moonshine(video_path, app_data_dir, progress_callback=on_prog)
            if dur <= 0 and subs_data:
                dur = float(subs_data[-1].get("end", 0.0)) + 3.0

            with open(get_subs_path(v_name), "w", encoding="utf-8") as f:
                json.dump({"duration": dur, "segments": subs_data}, f, ensure_ascii=False, indent=2)

            state["subtitles"] = subs_data
            state["duration_sec"] = dur
            time_slider.max = dur if dur > 0 else 100

            if len(subs_data) == 0:
                status_label.value = "⚠️ Video không có giọng nói tiếng Anh."
            else:
                status_label.value = f"✅ Hoàn tất! Đã nạp {len(subs_data)} câu phụ đề."
                build_transcript_list()
                seek_to_sec(0)
        except Exception as err:
            status_label.value = f"❌ Lỗi: {str(err)}"
        finally:
            loading_ring.visible = False
            page.update()

    def load_selected_video(file_path: str, name: str):
        state["current_video_name"] = name
        state["current_video_path"] = file_path
        state["vocab_list"] = load_vocab(name)
        state["current_pos_sec"] = 0.0
        state["active_sub_idx"] = -1
        state["is_playing"] = True
        play_btn.icon = ft.Icons.PAUSE

        new_video = ft.Video(
            playlist=[ft.VideoMedia(file_path)],
            aspect_ratio=16/9,
            fill_color=ft.Colors.BLACK,
            volume=100,
            autoplay=True,
            show_controls=False
        )
        state["active_video_ref"] = new_video

        video_stack = ft.Stack(
            controls=[
                new_video,
                ft.Container(
                    content=overlay_sub_box,
                    bottom=12,
                    left=10,
                    right=10,
                    alignment=ft.alignment.center
                ),
                ft.Container(
                    content=btn_fs,
                    top=8,
                    right=8
                )
            ]
        )
        video_container.content = video_stack
        page.update()

        # 1. Kiểm tra file phụ đề nội bộ trong app
        subs_file = get_subs_path(name)
        # 2. Hoặc file .json nằm cùng thư mục ngoài với video
        external_json = os.path.splitext(file_path)[0] + ".json"
        target_sub_file = subs_file if os.path.exists(subs_file) else (external_json if os.path.exists(external_json) else None)

        subs_loaded = False
        if target_sub_file:
            try:
                with open(target_sub_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        state["subtitles"] = data.get("segments", [])
                        state["duration_sec"] = float(data.get("duration", 0.0))
                    elif isinstance(data, list):
                        state["subtitles"] = data
                        state["duration_sec"] = 0.0
                subs_loaded = True
            except Exception:
                state["subtitles"] = []

        if subs_loaded and state["subtitles"]:
            if state["duration_sec"] <= 0:
                last_end = float(state["subtitles"][-1].get("end", 0.0))
                state["duration_sec"] = last_end + 3.0

            time_slider.max = state["duration_sec"] if state["duration_sec"] > 0 else 100
            status_label.value = f"✅ Đã nạp {len(state['subtitles'])} câu phụ đề."
            build_transcript_list()
            seek_to_sec(0.0)
        else:
            if HAVE_MOONSHINE_API:
                threading.Thread(target=process_subtitles_moonshine, args=(file_path, name), daemon=True).start()
            else:
                status_label.value = "ℹ️ Hãy để file phụ đề .json cùng tên với video."
                state["subtitles"] = []
                build_transcript_list()

        refresh_vocab_ui()
        page.update()

    def on_file_picked(e: ft.FilePickerResultEvent):
        if e.files and len(e.files) > 0:
            f = e.files[0]
            load_selected_video(f.path, f.name)

    file_picker = ft.FilePicker(on_result=on_file_picked)
    page.overlay.append(file_picker)

    # ================= GIAO DIỆN ÔN TẬP TỪ VỰNG =================
    quiz_area = ft.Column(spacing=14)
    saved_vocab_col = ft.Column(scroll=ft.ScrollMode.AUTO, spacing=6)

    def refresh_vocab_ui():
        v_list = state["vocab_list"]
        saved_vocab_col.controls.clear()
        if not v_list:
            saved_vocab_col.controls.append(ft.Text("Chưa có từ vựng nào.", color=ft.Colors.GREY_500))
        else:
            for item in list(v_list):
                def del_word(e, w_en=item["en"]):
                    state["vocab_list"] = [x for x in state["vocab_list"] if x["en"] != w_en]
                    save_vocab(state["current_video_name"], state["vocab_list"])
                    refresh_vocab_ui()
                    page.update()

                card = ft.Container(
                    content=ft.Row([
                        ft.Column([
                            ft.Text(item["en"], size=16, weight=ft.FontWeight.BOLD),
                            ft.Text(item["vi"], size=13, color=ft.Colors.GREY_400)
                        ], expand=True),
                        ft.IconButton(ft.Icons.VOLUME_UP, on_click=lambda _, w=item["en"]: speak(w), icon_size=20),
                        ft.IconButton(ft.Icons.DELETE_OUTLINE, on_click=del_word, icon_color=ft.Colors.RED_400, icon_size=20)
                    ]),
                    padding=10,
                    bgcolor=ft.Colors.WHITE10,
                    border_radius=8
                )
                saved_vocab_col.controls.append(card)

        if v_list:
            state["q_en_idx"] = random.randint(0, len(v_list) - 1)
            state["q_vi_idx"] = random.randint(0, len(v_list) - 1)
            render_quiz_cards()
        else:
            quiz_area.controls = [ft.Text("Cần ít nhất 1 từ vựng để ôn tập.", color=ft.Colors.GREY_500)]
        page.update()

    def render_quiz_cards():
        v_list = state["vocab_list"]
        if not v_list:
            return
        w_en = v_list[state["q_en_idx"] % len(v_list)]
        w_vi = v_list[state["q_vi_idx"] % len(v_list)]

        res_en_text = ft.Text("", size=13)
        def check_en(e):
            if check_en_to_vi_smart(ans_en_input.value, w_en["en"], w_en["vi"], offline_dict):
                res_en_text.value = f"🎉 Đúng! Chuẩn: {w_en['vi']}"
                res_en_text.color = ft.Colors.GREEN_400
            else:
                res_en_text.value = f"❌ Sai! Gợi ý: {w_en['vi']}"
                res_en_text.color = ft.Colors.RED_400
            page.update()

        ans_en_input = ft.TextField(hint_text="Nghĩa tiếng Việt...", text_size=14, dense=True, on_submit=check_en)

        card_en = ft.Container(
            content=ft.Column([
                ft.Text("🇬🇧 ➔ 🇻🇳 Dịch sang Tiếng Việt", size=13, color=ft.Colors.LIGHT_BLUE_200, weight=ft.FontWeight.BOLD),
                ft.Row([
                    ft.Text(w_en["en"], size=22, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                    ft.IconButton(ft.Icons.VOLUME_UP, on_click=lambda _: speak(w_en["en"]), icon_color=ft.Colors.AMBER)
                ]),
                ans_en_input,
                ft.Row([
                    ft.ElevatedButton("Kiểm tra", on_click=check_en),
                    ft.TextButton("Xem đáp án", on_click=lambda _: setattr(res_en_text, "value", f"Đáp án: {w_en['vi']}") or page.update()),
                    ft.TextButton("Từ khác ➡️", on_click=lambda _: refresh_vocab_ui())
                ]),
                res_en_text
            ], spacing=6),
            padding=12,
            bgcolor=ft.Colors.BLUE_GREY_900,
            border_radius=10
        )

        res_vi_text = ft.Text("", size=13)
        def check_vi(e):
            if check_vi_to_en_smart(ans_vi_input.value, w_vi["vi"], w_vi["en"], v_list, offline_dict):
                res_vi_text.value = f"🎉 Xuất sắc! Từ: {w_vi['en']}"
                res_vi_text.color = ft.Colors.GREEN_400
            else:
                res_vi_text.value = f"❌ Chưa đúng! Từ gốc: {w_vi['en']}"
                res_vi_text.color = ft.Colors.RED_400
            page.update()

        ans_vi_input = ft.TextField(hint_text="Từ tiếng Anh...", text_size=14, dense=True, on_submit=check_vi)

        card_vi = ft.Container(
            content=ft.Column([
                ft.Text("🇻🇳 ➔ 🇬🇧 Dịch sang Tiếng Anh", size=13, color=ft.Colors.AMBER_200, weight=ft.FontWeight.BOLD),
                ft.Text(w_vi["vi"], size=18, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                ft.Text(f"Gợi ý: {w_vi['hint']}", size=12, color=ft.Colors.YELLOW_600),
                ans_vi_input,
                ft.Row([
                    ft.ElevatedButton("Kiểm tra", on_click=check_vi),
                    ft.TextButton("Xem đáp án", on_click=lambda _: setattr(res_vi_text, "value", f"Đáp án: {w_vi['en']}") or page.update()),
                    ft.TextButton("Từ khác ➡️", on_click=lambda _: refresh_vocab_ui())
                ]),
                res_vi_text
            ], spacing=6),
            padding=12,
            bgcolor=ft.Colors.BROWN_900,
            border_radius=10
        )

        quiz_area.controls = [card_en, card_vi]
        page.update()

    # ================= TAB CÀI ĐẶT =================
    settings_view = ft.Container(
        content=ft.Column([
            ft.Text("🌙 Trạng Thái Ứng Dụng", size=16, weight=ft.FontWeight.BOLD),
            ft.Text("• Phiên bản: 1.0.0 Mobile Ready", size=14, color=ft.Colors.GREEN_400),
            ft.Text("• Hỗ trợ phát video kèm phụ đề tương tác", size=14, color=ft.Colors.LIGHT_BLUE_200),
            ft.Text("• Tra từ điển & Lưu sổ tay từ vựng học tập", size=14, color=ft.Colors.AMBER_200),
            ft.Divider(height=20),
            ft.Text(f"Thư mục lưu trữ: {app_data_dir}", size=11, color=ft.Colors.GREY_500)
        ], spacing=10),
        padding=16
    )

    top_bar = ft.Container(
        content=ft.ElevatedButton(
            "📂 Mở video trong máy",
            icon=ft.Icons.FILE_OPEN,
            on_click=lambda _: file_picker.pick_files(allow_multiple=False, file_type=ft.FilePickerFileType.VIDEO),
            width=float("inf")
        ),
        padding=ft.padding.symmetric(horizontal=12, vertical=6)
    )

    transcript_section = ft.Container(
        content=ft.Column([
            ft.Text("📜 Phụ đề đầy đủ (Bấm câu để tua và tra từ):", size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_400),
            ft.Container(content=transcript_col, height=220)
        ]),
        padding=10
    )

    watch_view = ft.Column([
        top_bar,
        ft.Row([loading_ring, status_label], alignment=ft.MainAxisAlignment.CENTER),
        video_container,
        controls_bar,
        transcript_section
    ], scroll=ft.ScrollMode.AUTO, expand=True)

    vocab_view = ft.Container(
        content=ft.Column([
            ft.Text("🎯 Thử Thách Luyện Dịch", size=18, weight=ft.FontWeight.BOLD),
            quiz_area,
            ft.Divider(),
            ft.Text("📋 Sổ Từ Vựng Của Video", size=16, weight=ft.FontWeight.BOLD),
            saved_vocab_col
        ], scroll=ft.ScrollMode.AUTO),
        padding=12,
        expand=True
    )

    main_container = ft.Container(content=watch_view, expand=True)

    def on_nav_change(e):
        idx = page.navigation_bar.selected_index
        if idx == 0:
            main_container.content = watch_view
        elif idx == 1:
            main_container.content = vocab_view
            refresh_vocab_ui()
        elif idx == 2:
            main_container.content = settings_view
        page.update()

    page.navigation_bar = ft.NavigationBar(
        selected_index=0,
        on_change=on_nav_change,
        destinations=[
            ft.NavigationBarDestination(icon=ft.Icons.SMART_DISPLAY, label="Xem Video"),
            ft.NavigationBarDestination(icon=ft.Icons.EDIT_NOTE, label="Ôn Từ Vựng"),
            ft.NavigationBarDestination(icon=ft.Icons.SETTINGS, label="Cài Đặt")
        ]
    )

    page.add(main_container)

if __name__ == "__main__":
    ft.app(target=main)
