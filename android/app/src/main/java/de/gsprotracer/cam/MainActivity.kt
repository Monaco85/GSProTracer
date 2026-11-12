package de.gsprotracer.cam

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.RectF
import android.graphics.SurfaceTexture
import android.graphics.drawable.GradientDrawable
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.view.TextureView
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : Activity(), HighSpeedRecorder.Listener, PcLink.Listener {

    private lateinit var texture: TextureView
    private lateinit var status: TextView
    private lateinit var ipField: EditText
    private lateinit var btnRotate: Button
    private lateinit var btnLock: Button
    private lateinit var btnDim: Button
    private lateinit var flash: View

    private lateinit var recorder: HighSpeedRecorder
    private lateinit var link: PcLink
    private lateinit var clipDir: File

    private var camText = "Kamera startet …"
    private var linkText = "PC: –"
    private var clipText = ""
    private var uploadText = ""
    private var locked = false
    private var dimmed = false
    private var resumed = false

    private val prefs by lazy { getSharedPreferences("settings", Context.MODE_PRIVATE) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        clipDir = File(getExternalFilesDir(null) ?: filesDir, "clips").apply { mkdirs() }

        buildUi()
        recorder = HighSpeedRecorder(this, this)
        recorder.extraRotation = prefs.getInt("rotation", 0)
        link = PcLink(this)
        link.host = prefs.getString("host", "") ?: ""
        link.uploadPort = prefs.getInt("upload_port", 8090)
        link.start(clipDir)

        texture.surfaceTextureListener = object : TextureView.SurfaceTextureListener {
            override fun onSurfaceTextureAvailable(st: SurfaceTexture, w: Int, h: Int) { configureTransform(w, h); startCameraIfReady() }
            override fun onSurfaceTextureSizeChanged(st: SurfaceTexture, w: Int, h: Int) = configureTransform(w, h)
            override fun onSurfaceTextureDestroyed(st: SurfaceTexture) = true
            override fun onSurfaceTextureUpdated(st: SurfaceTexture) {}
        }
        updateRotateButton()
        refreshStatus()
        val ticker = object : Runnable {
            override fun run() { refreshStatus(); status.postDelayed(this, 1000) }
        }
        status.post(ticker)
    }

    // ------------------------------------------------------------------ UI

    private fun buildUi() {
        val root = FrameLayout(this).apply { setBackgroundColor(Color.BLACK) }
        texture = TextureView(this)
        root.addView(texture, FrameLayout.LayoutParams(1, 1, Gravity.CENTER))
        root.addOnLayoutChangeListener { _, l, t, r, b, _, _, _, _ ->
            // groesstes 16:9-Rechteck -> komplettes Bild sichtbar, nichts abgeschnitten
            val w = r - l; val h = b - t
            val (tw, th) = if (w * 9 > h * 16) Pair(h * 16 / 9, h) else Pair(w, w * 9 / 16)
            val lp = texture.layoutParams as FrameLayout.LayoutParams
            if (lp.width != tw || lp.height != th) {
                lp.width = tw; lp.height = th
                texture.post { texture.layoutParams = lp }
            }
        }

        flash = View(this).apply {
            background = GradientDrawable().apply { setStroke(18, Color.rgb(63, 185, 122)); setColor(Color.TRANSPARENT) }
            alpha = 0f
        }
        root.addView(flash, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        status = TextView(this).apply {
            setTextColor(Color.WHITE)
            textSize = 13f
            setBackgroundColor(Color.argb(150, 0, 0, 0))
            setPadding(16, 10, 16, 10)
        }
        root.addView(status, FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP or Gravity.START))

        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setBackgroundColor(Color.argb(150, 0, 0, 0))
            setPadding(8, 4, 8, 4)
            gravity = Gravity.CENTER_VERTICAL
        }
        ipField = EditText(this).apply {
            hint = "PC-IP z.B. 192.168.1.20"
            setText(prefs.getString("host", ""))
            setTextColor(Color.WHITE); setHintTextColor(Color.GRAY)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            imeOptions = EditorInfo.IME_ACTION_DONE or EditorInfo.IME_FLAG_NO_EXTRACT_UI
            minWidth = 380
            setOnEditorActionListener { _, _, _ -> applyHost(); true }
        }
        bar.addView(ipField)
        fun button(text: String, onClick: () -> Unit) = Button(this).apply {
            this.text = text; isAllCaps = false; setOnClickListener { onClick() }
        }.also { bar.addView(it) }
        button("Verbinden") { applyHost() }
        button("Testclip") { onTrigger("test-" + SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date()), 2.0, 1.0, 0.0) }
        btnRotate = button("Drehung") { cycleRotation() }
        btnLock = button("Belichtung/Fokus sperren") { toggleLock() }
        btnDim = button("Display dimmen") { toggleDim() }
        button("Update") { showUpdateDialog() }
        val scroller = android.widget.HorizontalScrollView(this).apply { addView(bar) }
        root.addView(scroller, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM))

        setContentView(root)
    }

    private fun applyHost() {
        val h = ipField.text.toString().trim()
        prefs.edit().putString("host", h).apply()
        link.host = h
        link.reconnect()
        (getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager).hideSoftInputFromWindow(ipField.windowToken, 0)
        ipField.clearFocus()
    }

    private fun cycleRotation() {
        val r = (prefs.getInt("rotation", 0) + 90) % 360
        prefs.edit().putInt("rotation", r).apply()
        recorder.extraRotation = r
        updateRotateButton()
    }

    private fun updateRotateButton() {
        btnRotate.text = "Video drehen: ${prefs.getInt("rotation", 0)}°"
    }

    private fun toggleLock() {
        locked = !locked
        recorder.setLocked(locked)
        btnLock.text = if (locked) "Sperre lösen" else "Belichtung/Fokus sperren"
    }

    private fun toggleDim() {
        dimmed = !dimmed
        val lp = window.attributes
        lp.screenBrightness = if (dimmed) 0.01f else WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE
        window.attributes = lp
        btnDim.text = if (dimmed) "Display hell" else "Display dimmen"
    }

    private fun showUpdateDialog() {
        val updater = Updater(this)
        val pad = 32
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(pad, pad / 2, pad, 0) }
        val info = TextView(this).apply {
            text = "Installiert: ${updater.installedName()} (Build ${updater.installedBuild()})"
        }
        val tokenField = EditText(this).apply {
            hint = "GitHub-Token (nur bei privatem Repo nötig)"
            setText(updater.token)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            imeOptions = EditorInfo.IME_FLAG_NO_EXTRACT_UI
        }
        box.addView(info); box.addView(tokenField)
        val dlg = android.app.AlertDialog.Builder(this)
            .setTitle("App-Update")
            .setView(box)
            .setPositiveButton("Nach Update suchen", null)
            .setNegativeButton("Schließen", null)
            .create()
        dlg.setOnShowListener {
            val btn = dlg.getButton(android.app.AlertDialog.BUTTON_POSITIVE)
            btn.setOnClickListener {
                updater.token = tokenField.text.toString()
                btn.isEnabled = false
                info.text = "Suche …"
                Thread {
                    val msg: String = try {
                        val r = updater.latest()
                        if (r.build <= updater.installedBuild()) {
                            "Aktuell – neuester Build ist ${r.build}."
                        } else {
                            val f = updater.download(r) { pct -> runOnUiThread { info.text = "Lade Build ${r.build} … $pct %" } }
                            runOnUiThread {
                                if (!updater.install(this, f))
                                    info.text = "Bitte „Unbekannte Apps installieren“ erlauben, dann erneut tippen."
                            }
                            "Build ${r.build} geladen – Installation bestätigen."
                        }
                    } catch (e: Exception) {
                        "⚠ ${e.message}"
                    }
                    runOnUiThread {
                        if (!info.text.startsWith("Bitte")) info.text = msg
                        btn.isEnabled = true
                    }
                }.start()
            }
        }
        dlg.show()
    }

    @Suppress("DEPRECATION")
    private fun displayRotation(): Int =
        if (Build.VERSION.SDK_INT >= 30) display?.rotation ?: 1 else windowManager.defaultDisplay.rotation

    /** Kamera liefert Bilder in Sensor-Ausrichtung -> fuer Querformat drehen (Camera2Basic-Logik). */
    private fun configureTransform(viewW: Int, viewH: Int) {
        if (viewW == 0 || viewH == 0) return
        val rotation = displayRotation()
        val size = recorder.mode?.size ?: HighSpeedRecorder.TARGET_SIZE
        val matrix = Matrix()
        val viewRect = RectF(0f, 0f, viewW.toFloat(), viewH.toFloat())
        val bufferRect = RectF(0f, 0f, size.height.toFloat(), size.width.toFloat())
        val cx = viewRect.centerX(); val cy = viewRect.centerY()
        if (rotation == 1 || rotation == 3) {
            bufferRect.offset(cx - bufferRect.centerX(), cy - bufferRect.centerY())
            matrix.setRectToRect(viewRect, bufferRect, Matrix.ScaleToFit.FILL)
            val scale = maxOf(viewH.toFloat() / size.height, viewW.toFloat() / size.width)
            matrix.postScale(scale, scale, cx, cy)
            matrix.postRotate(90f * (rotation - 2), cx, cy)
        } else if (rotation == 2) {
            matrix.postRotate(180f, cx, cy)
        }
        texture.setTransform(matrix)
    }

    // ------------------------------------------------------------------ Lifecycle

    override fun onResume() {
        super.onResume()
        resumed = true
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.CAMERA), 1)
        } else startCameraIfReady()
    }

    override fun onPause() {
        resumed = false
        recorder.stop()
        camRunning = false
        super.onPause()
    }

    override fun onDestroy() {
        recorder.release()
        link.shutdown()
        super.onDestroy()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) startCameraIfReady()
        else { camText = "Ohne Kamera-Berechtigung geht es nicht."; refreshStatus() }
    }

    private var camRunning = false
    private fun startCameraIfReady() {
        if (!resumed || camRunning || !texture.isAvailable) return
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) return
        camRunning = true
        configureTransform(texture.width, texture.height)
        recorder.start(texture.surfaceTexture)
    }

    // ------------------------------------------------------------------ Status

    private fun batteryTemp(): Double? {
        val i: Intent? = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val t = i?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Int.MIN_VALUE) ?: Int.MIN_VALUE
        return if (t == Int.MIN_VALUE) null else t / 10.0
    }

    private fun refreshStatus() {
        val m = recorder.mode
        val fpsLine = if (m != null) "gemessen %.0f fps · Puffer %.1f s".format(recorder.measuredFps, recorder.bufferedSeconds()) else ""
        val temp = batteryTemp()?.let { "Akku %.0f °C".format(it) + if (it >= 43) "  ⚠ zu warm!" else "" } ?: ""
        val pending = link.pendingCount()
        status.text = listOf(
            linkText, camText, fpsLine, temp,
            if (pending > 0) "Uploads offen: $pending" else "",
            uploadText, clipText
        ).filter { it.isNotBlank() }.joinToString("\n")
    }

    private fun ui(block: () -> Unit) = runOnUiThread { block(); refreshStatus() }

    private fun flashBorder() {
        flash.animate().cancel()
        flash.alpha = 1f
        flash.animate().alpha(0f).setDuration(900).start()
    }

    // ------------------------------------------------------------------ HighSpeedRecorder.Listener

    override fun onCameraStatus(text: String) = ui {
        camText = text
        configureTransform(texture.width, texture.height) // Groesse steht erst jetzt fest (720p oder 1080p)
    }
    override fun onCameraError(text: String) = ui { camText = "⚠ $text"; camRunning = false }

    override fun onClipReady(clip: HighSpeedRecorder.ClipInfo) {
        link.queueUpload(clip, "${Build.MANUFACTURER} ${Build.MODEL}")
        ui { clipText = "Clip ${clip.shotId}: %.2f s gespeichert".format(clip.durationSec) }
    }

    override fun onClipFailed(shotId: String, reason: String) = ui { clipText = "⚠ Clip $shotId: $reason" }

    // ------------------------------------------------------------------ PcLink.Listener

    override fun onTrigger(shotId: String, pre: Double, post: Double, delay: Double) {
        // sofort (im Netzwerk-Thread) ausloesen -> Zeitpunkt so genau wie moeglich
        recorder.requestClip(shotId, pre, post, delay, clipDir)
        prefs.edit().putInt("upload_port", link.uploadPort).apply()
        ui { flashBorder(); clipText = "Schuss erkannt → Clip $shotId wird geschnitten …" }
    }

    override fun onLinkState(connected: Boolean, text: String) = ui { linkText = text }

    override fun onUploadState(pending: Int, lastMessage: String) = ui { uploadText = lastMessage }

    override fun statusJson(): JSONObject {
        val m = recorder.mode
        return JSONObject()
            .put("device", "${Build.MANUFACTURER} ${Build.MODEL}")
            .put("fps", m?.fps ?: 0)
            .put("measured_fps", recorder.measuredFps)
            .put("high_speed", m?.highSpeed ?: false)
            .put("width", m?.size?.width ?: 0)
            .put("height", m?.size?.height ?: 0)
            .put("buffer_s", recorder.bufferedSeconds())
            .put("temp", batteryTemp() ?: 0.0)
            .put("pending", link.pendingCount())
            .put("camera_info", recorder.cameraInfo)
    }
}
