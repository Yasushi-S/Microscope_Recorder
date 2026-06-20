"""
顕微鏡録画アプリ - 舌野クリニック
依存: pip install opencv-python pillow
"""

import cv2
import tkinter as tk
from tkinter import messagebox
import threading
import time
import os
import re
import subprocess
from datetime import datetime
from PIL import Image, ImageTk

# ── 設定 ────────────────────────────────────────────────
SAVE_DIR = r"C:\Users\shita\Videos\PC04顕微鏡画像"   # 保存先フォルダ（変更可）
CAMERA_INDEX = 0                      # USBカメラのインデックス（通常0）
PREVIEW_FPS = 30                      # プレビューFPS
RECORD_FPS = 30                       # 録画FPS（カメラが対応できる上限まで自動調整される）
PREVIEW_SIZE = (960, 540)            # プレビュー表示サイズ（画面表示用）
# このカメラ(HY-500B)は1920x1080を直接指定すると15fps上限モードに落ちる。
# 2048x1536(MJPG)であれば実測でも真の30fpsが出る（要CAP_MSMFバックエンド。
# 旧来のCAP_DSHOWはOpenCV側のMJPEGデコードがボトルネックとなり、解像度によらず
# 5～10fps程度に制限されてしまうことを実機検証で確認済み）。
# そのためカメラ取得は2048x1536/MSMFで行い、録画時のみ1920x1080にリサイズする。
CAPTURE_SIZE = (2048, 1536)          # カメラ取得解像度（MJPG 30fps対応・MSMF前提）
RECORD_SIZE = (1920, 1080)           # 録画（保存）解像度

# 露光設定（視野移動時のブレ対策）
MANUAL_EXPOSURE = True                # True: 露光時間を固定してブレを抑える / False: カメラのオートに任せる
EXPOSURE_VALUE = -9                   # 値が小さいほど露光時間が短くブレにくいが暗くなる（カメラ依存。要調整）

SPECIMEN_TYPES = [
    "帯下(生食)",
    "帯下(KOH)",
    "精液",
    "その他",
]

DEFAULT_DURATION = 30   # デフォルト録画秒数


# ── ファイル名生成 ────────────────────────────────────────
INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')
PATIENT_ID_PATTERN = re.compile(r'^\d{5}$')


def sanitize_for_filename(text: str) -> str:
    return INVALID_FILENAME_CHARS.sub("_", text).strip()


def generate_filename(patient_id: str, specimen: str, save_dir: str, ext: str = "mp4") -> str:
    date_str = datetime.now().strftime("%Y%m%d")
    safe_id = sanitize_for_filename(patient_id)
    safe_specimen = sanitize_for_filename(specimen)
    base = f"{date_str}_{safe_id}_{safe_specimen}"
    # 連番を検索して付与
    existing = [
        f for f in os.listdir(save_dir)
        if f.startswith(base) and f.endswith(f".{ext}")
    ]
    nums = []
    for f in existing:
        m = re.search(rf"_(\d+)\.{ext}$", f)
        if m:
            nums.append(int(m.group(1)))
    next_num = max(nums) + 1 if nums else 1
    return os.path.join(save_dir, f"{base}_{next_num:03d}.{ext}")


# ── メインアプリ ─────────────────────────────────────────
class MicroscopeApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("顕微鏡録画システム")
        self.root.configure(bg="#1a1a2e")
        self.root.resizable(False, False)

        self.cap = None
        self.recording = False
        self.ffmpeg_proc = None
        self.writer_lock = threading.Lock()
        self.last_frame = None
        self.frame_lock = threading.Lock()
        self.preview_thread = None
        self.record_writer_thread = None
        self.record_timer = None
        self.remaining = 0
        self.preview_active = False

        os.makedirs(SAVE_DIR, exist_ok=True)
        self._build_ui()
        self._start_preview()

    # ── UI構築 ───────────────────────────────────────────
    def _build_ui(self):
        root = self.root

        # ── タイトルバー
        header = tk.Frame(root, bg="#0f3460", pady=8)
        header.pack(fill="x")
        tk.Label(
            header, text="🔬  顕微鏡録画システム",
            font=("Meiryo UI", 14, "bold"),
            fg="#e0e0e0", bg="#0f3460"
        ).pack()

        # ── メインエリア
        main = tk.Frame(root, bg="#1a1a2e", padx=16, pady=12)
        main.pack(fill="both")

        # 左: プレビュー
        left = tk.Frame(main, bg="#1a1a2e")
        left.grid(row=0, column=0, rowspan=4, padx=(0, 16))

        self.canvas = tk.Canvas(left, width=PREVIEW_SIZE[0], height=PREVIEW_SIZE[1],
                                bg="#000", highlightthickness=2,
                                highlightbackground="#16213e")
        self.canvas.pack()

        self.rec_indicator = tk.Label(left, text="", bg="#1a1a2e",
                                      fg="#ff4444", font=("Meiryo UI", 10, "bold"))
        self.rec_indicator.pack(pady=(4, 0))

        # 右: コントロールパネル
        right = tk.Frame(main, bg="#1a1a2e")
        right.grid(row=0, column=1, sticky="n")

        def section_label(parent, text):
            tk.Label(parent, text=text, font=("Meiryo UI", 9),
                     fg="#7a8fa6", bg="#1a1a2e").pack(anchor="w", pady=(10, 2))

        def entry_field(parent):
            e = tk.Entry(parent, font=("Meiryo UI", 13),
                         bg="#16213e", fg="#e0e0e0",
                         insertbackground="#e0e0e0",
                         relief="flat", bd=0, width=20,
                         highlightthickness=1,
                         highlightbackground="#0f3460",
                         highlightcolor="#e94560")
            e.pack(ipady=6, fill="x")
            return e

        # 患者ID
        section_label(right, "患者ID")
        self.id_var = tk.StringVar()
        self.id_entry = entry_field(right)
        self.id_entry.config(textvariable=self.id_var)

        # 検体種別
        section_label(right, "検体種別")
        self.specimen_var = tk.StringVar(value=SPECIMEN_TYPES[0])
        spec_frame = tk.Frame(right, bg="#1a1a2e")
        spec_frame.pack(fill="x")
        for sp in SPECIMEN_TYPES:
            rb = tk.Radiobutton(
                spec_frame, text=sp, variable=self.specimen_var, value=sp,
                font=("Meiryo UI", 10),
                fg="#c0c0c0", bg="#1a1a2e",
                selectcolor="#0f3460",
                activebackground="#1a1a2e",
                activeforeground="#e94560"
            )
            rb.pack(anchor="w")

        # 録画時間
        section_label(right, "録画時間（秒）")
        dur_frame = tk.Frame(right, bg="#1a1a2e")
        dur_frame.pack(fill="x")
        self.duration_var = tk.IntVar(value=DEFAULT_DURATION)
        for sec in [15, 30, 60, 120]:
            b = tk.Radiobutton(
                dur_frame, text=f"{sec}秒", variable=self.duration_var, value=sec,
                font=("Meiryo UI", 10),
                fg="#c0c0c0", bg="#1a1a2e",
                selectcolor="#0f3460",
                activebackground="#1a1a2e",
                activeforeground="#e94560"
            )
            b.pack(side="left", padx=4)

        # カスタム秒数
        custom_frame = tk.Frame(right, bg="#1a1a2e")
        custom_frame.pack(fill="x", pady=(4, 0))
        tk.Label(custom_frame, text="カスタム:", font=("Meiryo UI", 9),
                 fg="#7a8fa6", bg="#1a1a2e").pack(side="left")
        self.custom_dur = tk.Entry(custom_frame, font=("Meiryo UI", 11),
                                   bg="#16213e", fg="#e0e0e0",
                                   insertbackground="#e0e0e0",
                                   width=5, relief="flat",
                                   highlightthickness=1,
                                   highlightbackground="#0f3460")
        self.custom_dur.pack(side="left", ipady=4, padx=4)
        tk.Label(custom_frame, text="秒", font=("Meiryo UI", 9),
                 fg="#7a8fa6", bg="#1a1a2e").pack(side="left")

        # カウントダウン表示
        self.countdown_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.countdown_var,
                 font=("Meiryo UI", 22, "bold"),
                 fg="#e94560", bg="#1a1a2e").pack(pady=(16, 4))

        # ファイル名プレビュー
        self.fname_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.fname_var,
                 font=("Meiryo UI", 8),
                 fg="#5a6a7a", bg="#1a1a2e",
                 wraplength=260, justify="left").pack(anchor="w")

        self.id_var.trace_add("write", lambda *_: self._update_fname_preview())
        self.specimen_var.trace_add("write", lambda *_: self._update_fname_preview())
        self._update_fname_preview()

        # 録画ボタン
        btn_frame = tk.Frame(right, bg="#1a1a2e")
        btn_frame.pack(pady=16, fill="x")

        self.rec_btn = tk.Button(
            btn_frame,
            text="⏺  録画開始",
            font=("Meiryo UI", 13, "bold"),
            bg="#e94560", fg="white",
            activebackground="#c73652", activeforeground="white",
            relief="flat", bd=0, cursor="hand2",
            command=self._toggle_record
        )
        self.rec_btn.pack(fill="x", ipady=10)

        self.photo_btn = tk.Button(
            btn_frame,
            text="📷  静止画撮影",
            font=("Meiryo UI", 13, "bold"),
            bg="#0f3460", fg="white",
            activebackground="#16213e", activeforeground="white",
            relief="flat", bd=0, cursor="hand2",
            command=self._capture_photo
        )
        self.photo_btn.pack(fill="x", ipady=10, pady=(8, 0))

        self.next_patient_btn = tk.Button(
            btn_frame,
            text="👤  次の患者",
            font=("Meiryo UI", 11),
            bg="#16213e", fg="#c0c0c0",
            activebackground="#0f3460", activeforeground="white",
            relief="flat", bd=0, cursor="hand2",
            command=self._next_patient
        )
        self.next_patient_btn.pack(fill="x", ipady=8, pady=(8, 0))

        # 撮影ステータス表示
        self.capture_status_var = tk.StringVar(value="")
        tk.Label(right, textvariable=self.capture_status_var,
                 font=("Meiryo UI", 9, "bold"),
                 fg="#4caf50", bg="#1a1a2e").pack(anchor="w", pady=(4, 0))

        # 保存フォルダ表示
        tk.Label(right, text=f"保存先: {SAVE_DIR}", font=("Meiryo UI", 8),
                 fg="#3a4a5a", bg="#1a1a2e").pack(anchor="w")

        # 保存済みファイル一覧
        section_label(left, "本日の保存済みファイル（新しい順）")
        list_frame = tk.Frame(left, bg="#1a1a2e")
        list_frame.pack(fill="x")

        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")

        self.file_listbox = tk.Listbox(
            list_frame,
            font=("Meiryo UI", 9),
            bg="#16213e", fg="#e0e0e0",
            selectbackground="#0f3460",
            activestyle="none",
            relief="flat", bd=0, height=5,
            highlightthickness=1,
            highlightbackground="#0f3460",
            yscrollcommand=scrollbar.set,
        )
        self.file_listbox.pack(side="left", fill="x", expand=True)
        scrollbar.config(command=self.file_listbox.yview)

        self._refresh_file_list()

    def _refresh_file_list(self):
        today = datetime.now().strftime("%Y%m%d")
        try:
            files = [
                f for f in os.listdir(SAVE_DIR)
                if f.startswith(today) and f.endswith((".mp4", ".jpg"))
            ]
        except OSError:
            files = []
        files.sort(key=lambda f: os.path.getmtime(os.path.join(SAVE_DIR, f)), reverse=True)
        self.file_listbox.delete(0, tk.END)
        for f in files:
            icon = "🎬" if f.endswith(".mp4") else "📷"
            self.file_listbox.insert(tk.END, f"{icon} {f}")

    def _update_fname_preview(self):
        pid = sanitize_for_filename(self.id_var.get().strip()) or "ID"
        sp = self.specimen_var.get()
        date_str = datetime.now().strftime("%Y%m%d")
        safe_sp = sanitize_for_filename(sp)
        base = f"{date_str}_{pid}_{safe_sp}_001"
        self.fname_var.set(f"例: {base}.mp4 / {base}.jpg")

    # ── カメラプレビュー ─────────────────────────────────
    def _start_preview(self):
        # CAP_DSHOWはこのカメラだとMJPEGデコードがボトルネックになり解像度に関わらず
        # 5～10fps程度に制限される（実機検証済み）。CAP_MSMFだと同じ解像度で実測30fps出る。
        self.cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_MSMF)
        if not self.cap.isOpened():
            messagebox.showerror("カメラエラー",
                                 f"カメラ(index={CAMERA_INDEX})を開けません。\n"
                                 "USBキャプチャデバイスを確認してください。")
            self.cap.release()
            self.cap = None
            self.rec_btn.config(state="disabled")
            self.photo_btn.config(state="disabled")
            return
        # MJPG指定でUSB帯域不足による1080pキャプチャの遅延・カクつきを回避
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAPTURE_SIZE[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAPTURE_SIZE[1])
        self.cap.set(cv2.CAP_PROP_FPS, RECORD_FPS)
        if MANUAL_EXPOSURE:
            # MSMFでは露光値のスケールがDirectShowと異なるため、EXPOSURE_VALUEは要調整
            self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
            self.cap.set(cv2.CAP_PROP_EXPOSURE, EXPOSURE_VALUE)
        self.preview_active = True
        self.preview_thread = threading.Thread(target=self._preview_loop, daemon=True)
        self.preview_thread.start()

    def _preview_loop(self):
        preview_interval = 1.0 / PREVIEW_FPS
        last_preview_time = 0.0
        while self.preview_active:
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.01)
                continue
            with self.frame_lock:
                self.last_frame = frame
            # プレビュー更新はPREVIEW_FPS に独立して制限
            now = time.monotonic()
            if now - last_preview_time >= preview_interval:
                last_preview_time = now
                preview_frame = cv2.resize(frame, PREVIEW_SIZE)
                frame_rgb = cv2.cvtColor(preview_frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame_rgb)
                # PhotoImageの生成・キャンバス更新はメインスレッドで行う
                self.canvas.after(0, self._update_canvas, img)

    def _update_canvas(self, img):
        imgtk = ImageTk.PhotoImage(image=img)
        self.canvas._imgtk = imgtk  # GC防止
        if not hasattr(self, "_img_id"):
            self._img_id = self.canvas.create_image(0, 0, anchor="nw", image=imgtk)
        else:
            self.canvas.itemconfig(self._img_id, image=imgtk)

    # ── 録画書き込み（実時間に同期させてフレームを送出） ─────
    def _record_writer_loop(self, proc):
        # カメラの実際の取得速度に関わらず、実時間どおりの再生速度になるよう
        # 一定間隔（RECORD_FPS）で最新フレームをffmpegへ送出する。
        # カメラが遅い場合は直前フレームを再送（複製）し、速い場合は間引く。
        interval = 1.0 / RECORD_FPS
        next_time = time.monotonic()
        while True:
            with self.writer_lock:
                if not self.recording or self.ffmpeg_proc is not proc:
                    break
            with self.frame_lock:
                frame = self.last_frame
            if frame is not None:
                try:
                    proc.stdin.write(cv2.resize(frame, RECORD_SIZE).tobytes())
                except OSError:
                    break
            next_time += interval
            sleep_time = next_time - time.monotonic()
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                next_time = time.monotonic()

    # ── 静止画撮影 ───────────────────────────────────────
    def _capture_photo(self):
        if self.cap is None or not self.cap.isOpened():
            messagebox.showerror("カメラエラー", "カメラが利用できないため撮影できません。")
            return

        patient_id = self.id_var.get().strip()
        if not PATIENT_ID_PATTERN.match(patient_id):
            messagebox.showwarning("入力エラー", "患者IDは5桁の数字で入力してください。")
            return

        with self.frame_lock:
            frame = self.last_frame

        if frame is None:
            messagebox.showerror("撮影エラー", "映像を取得できませんでした。")
            return

        filepath = generate_filename(patient_id, self.specimen_var.get(), SAVE_DIR, ext="jpg")
        if not cv2.imwrite(filepath, frame):
            messagebox.showerror("撮影エラー",
                                 "画像を保存できませんでした。\n"
                                 "保存先フォルダの権限や空き容量を確認してください。")
            return

        self._flash_canvas()
        fname = os.path.basename(filepath)
        self.capture_status_var.set(f"📷 保存しました: {fname}")
        self.root.after(3000, lambda: self.capture_status_var.set(""))
        self._refresh_file_list()

    def _flash_canvas(self):
        self.canvas.config(highlightbackground="#ffffff")
        self.root.after(120, lambda: self.canvas.config(highlightbackground="#16213e"))

    # ── 患者切替 ─────────────────────────────────────────
    def _next_patient(self):
        self.id_var.set("")
        self.specimen_var.set(SPECIMEN_TYPES[0])
        self.id_entry.focus_set()

    # ── 録画制御 ─────────────────────────────────────────
    def _toggle_record(self):
        if self.recording:
            self._stop_record()
        else:
            self._start_record()

    def _start_record(self):
        if self.cap is None or not self.cap.isOpened():
            messagebox.showerror("カメラエラー", "カメラが利用できないため録画できません。")
            return

        patient_id = self.id_var.get().strip()
        if not PATIENT_ID_PATTERN.match(patient_id):
            messagebox.showwarning("入力エラー", "患者IDは5桁の数字で入力してください。")
            return

        # 録画時間の取得
        custom = self.custom_dur.get().strip()
        if custom:
            try:
                duration = int(custom)
                if duration <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("入力エラー", "カスタム秒数は正の整数で入力してください。")
                return
        else:
            duration = self.duration_var.get()

        filepath = generate_filename(patient_id, self.specimen_var.get(), SAVE_DIR)
        cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo",
            "-s", f"{RECORD_SIZE[0]}x{RECORD_SIZE[1]}",
            "-pix_fmt", "bgr24",
            "-r", str(RECORD_FPS),
            "-i", "pipe:",
            "-vcodec", "h264_amf",
            "-usage", "transcoding",
            "-quality", "quality",
            "-rc", "cqp",
            "-qp_i", "18",
            "-qp_p", "20",
            "-pix_fmt", "yuv420p",
            filepath,
        ]
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     creationflags=creationflags)
        except Exception as e:
            messagebox.showerror("録画エラー",
                                 f"ffmpegを起動できませんでした。\n{e}")
            return

        with self.writer_lock:
            self.ffmpeg_proc = proc
            self.recording = True
        self.record_writer_thread = threading.Thread(
            target=self._record_writer_loop, args=(proc,), daemon=True
        )
        self.record_writer_thread.start()
        self.remaining = duration
        self.rec_btn.config(text="⏹  録画停止", bg="#444")
        self.rec_indicator.config(text="● REC")
        self.next_patient_btn.config(state="disabled")
        self._tick(filepath)

    def _tick(self, filepath):
        if not self.recording:
            return
        self.countdown_var.set(f"{self.remaining}秒")
        if self.remaining <= 0:
            self._stop_record(filepath=filepath, auto=True)
            return
        self.remaining -= 1
        self.record_timer = self.root.after(1000, self._tick, filepath)

    def _stop_record(self, filepath="", auto=False):
        if self.record_timer:
            self.root.after_cancel(self.record_timer)
            self.record_timer = None
        with self.writer_lock:
            self.recording = False
            proc = self.ffmpeg_proc
            self.ffmpeg_proc = None

        self.rec_btn.config(text="⏺  録画開始", bg="#e94560", state="disabled")
        self.rec_indicator.config(text="● 保存中...")
        self.countdown_var.set("")
        self.next_patient_btn.config(state="disabled")

        def finalize():
            if self.record_writer_thread:
                self.record_writer_thread.join(timeout=2.0)
                self.record_writer_thread = None
            if proc:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
                proc.wait()
            self.root.after(0, lambda: self._on_record_finalized(filepath, auto))

        threading.Thread(target=finalize, daemon=True).start()

    def _on_record_finalized(self, filepath, auto):
        self.rec_btn.config(text="⏺  録画開始", bg="#e94560", state="normal")
        self.rec_indicator.config(text="")
        self.next_patient_btn.config(state="normal")
        self._refresh_file_list()
        if auto and filepath:
            fname = os.path.basename(filepath)
            messagebox.showinfo("録画完了", f"保存しました:\n{fname}")

    # ── 終了処理 ─────────────────────────────────────────
    def on_close(self):
        self.preview_active = False
        with self.writer_lock:
            self.recording = False
            proc = self.ffmpeg_proc
            self.ffmpeg_proc = None
        if self.preview_thread:
            self.preview_thread.join(timeout=1.0)
        if self.record_writer_thread:
            self.record_writer_thread.join(timeout=2.0)
            self.record_writer_thread = None
        if proc:
            try:
                proc.stdin.close()
            except OSError:
                pass
            proc.wait(timeout=5.0)
        if self.cap:
            self.cap.release()
        self.root.destroy()


# ── エントリーポイント ───────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    app = MicroscopeApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
