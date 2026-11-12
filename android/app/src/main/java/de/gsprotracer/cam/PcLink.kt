package de.gsprotracer.cam

import android.util.Log
import org.json.JSONObject
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URL
import java.net.URLEncoder
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Steuerverbindung zum PC (TCP, eine JSON-Nachricht pro Zeile) und Upload der Clips per HTTP.
 * Verbindet sich automatisch neu, wenn der PC-Server neu startet oder das WLAN kurz weg ist.
 */
class PcLink(private val listener: Listener) {

    interface Listener {
        fun onTrigger(shotId: String, pre: Double, post: Double, delay: Double)
        fun onLinkState(connected: Boolean, text: String)
        fun statusJson(): JSONObject
        fun onUploadState(pending: Int, lastMessage: String)
    }

    companion object { private const val TAG = "PcLink" }

    @Volatile var host: String = ""
    @Volatile var controlPort: Int = 9300
    @Volatile var uploadPort: Int = 8090
    @Volatile var connected = false; private set

    @Volatile private var running = false
    @Volatile private var socket: Socket? = null
    private var out: OutputStream? = null
    private val writeLock = Any()
    private var thread: Thread? = null
    private val scheduler = Executors.newSingleThreadScheduledExecutor()
    private val uploader = Executors.newSingleThreadExecutor()
    @Volatile private var uploadDir: File? = null
    @Volatile private var uploading = false

    fun start(clipDir: File) {
        if (running) return
        running = true
        uploadDir = clipDir
        thread = Thread({ loop() }, "pc-link").apply { start() }
        scheduler.scheduleWithFixedDelay({ send(listener.statusJson().put("type", "status")) }, 2, 2, TimeUnit.SECONDS)
        scheduler.scheduleWithFixedDelay({ uploadPending() }, 3, 10, TimeUnit.SECONDS)
    }

    fun stop() {
        running = false
        try { socket?.close() } catch (_: Exception) {}
        thread?.interrupt()
    }

    fun shutdown() {
        stop()
        scheduler.shutdownNow()
        uploader.shutdownNow()
    }

    /** Verbindung sofort neu aufbauen (z.B. nach Aenderung der IP). */
    fun reconnect() {
        try { socket?.close() } catch (_: Exception) {}
    }

    private fun loop() {
        while (running) {
            val h = host
            if (h.isBlank()) {
                listener.onLinkState(false, "PC-IP eintragen")
                sleep(1000); continue
            }
            try {
                val s = Socket()
                s.tcpNoDelay = true
                s.keepAlive = true
                s.connect(InetSocketAddress(h, controlPort), 3000)
                socket = s
                synchronized(writeLock) { out = s.getOutputStream() }
                connected = true
                listener.onLinkState(true, "PC verbunden ($h)")
                send(listener.statusJson().put("type", "hello"))
                uploadPending()
                val reader = BufferedReader(InputStreamReader(s.getInputStream(), Charsets.UTF_8))
                while (running) {
                    val line = reader.readLine() ?: break
                    if (line.isBlank()) continue
                    handle(line)
                }
            } catch (e: Exception) {
                Log.i(TAG, "Verbindung: ${e.message}")
            } finally {
                connected = false
                synchronized(writeLock) { out = null }
                try { socket?.close() } catch (_: Exception) {}
                socket = null
            }
            if (running) {
                listener.onLinkState(false, "PC nicht erreichbar ($h:$controlPort) - neuer Versuch …")
                sleep(2000)
            }
        }
    }

    private fun handle(line: String) {
        val msg = try { JSONObject(line) } catch (_: Exception) { return }
        when (msg.optString("type")) {
            "trigger" -> {
                msg.optInt("upload_port", 0).takeIf { it > 0 }?.let { uploadPort = it }
                listener.onTrigger(
                    msg.getString("shot_id"),
                    msg.optDouble("pre", 4.0),
                    msg.optDouble("post", 0.5),
                    msg.optDouble("delay", 0.0),
                )
            }
        }
    }

    fun send(obj: JSONObject) {
        val bytes = (obj.toString() + "\n").toByteArray(Charsets.UTF_8)
        synchronized(writeLock) {
            val o = out ?: return
            try { o.write(bytes); o.flush() } catch (_: Exception) {
                try { socket?.close() } catch (_: Exception) {}
            }
        }
    }

    // ------------------------------------------------------------------ Upload

    /** Metadaten neben dem Clip ablegen und Upload anstossen. */
    fun queueUpload(clip: HighSpeedRecorder.ClipInfo, device: String) {
        val meta = JSONObject()
            .put("shot", clip.shotId)
            .put("fps", clip.fps)
            .put("w", clip.width)
            .put("h", clip.height)
            .put("dur", "%.4f".format(java.util.Locale.US, clip.durationSec))
            .put("trigger", "%.4f".format(java.util.Locale.US, clip.triggerTimeSec))
            .put("delay", clip.delaySec)
            .put("device", device)
        File(clip.file.parentFile, clip.file.nameWithoutExtension + ".json").writeText(meta.toString())
        uploadPending()
    }

    fun pendingCount(): Int = uploadDir?.listFiles { f -> f.name.endsWith(".mp4") }?.size ?: 0

    private fun uploadPending() {
        uploader.execute {
            if (uploading) return@execute
            uploading = true
            try {
                val dir = uploadDir ?: return@execute
                val files = dir.listFiles { f -> f.name.endsWith(".mp4") }?.sortedBy { it.name } ?: return@execute
                for (f in files) {
                    if (!running || host.isBlank()) break
                    val metaFile = File(dir, f.nameWithoutExtension + ".json")
                    val meta = if (metaFile.exists()) JSONObject(metaFile.readText()) else JSONObject().put("shot", f.nameWithoutExtension)
                    if (upload(f, meta)) {
                        f.delete(); metaFile.delete()
                        listener.onUploadState(pendingCount(), "Clip ${meta.optString("shot")} am PC ✓")
                    } else {
                        listener.onUploadState(pendingCount(), "Upload fehlgeschlagen - wird wiederholt")
                        break
                    }
                }
            } finally {
                uploading = false
            }
        }
    }

    private fun upload(file: File, meta: JSONObject): Boolean {
        val q = meta.keys().asSequence().joinToString("&") { k ->
            "$k=" + URLEncoder.encode(meta.get(k).toString(), "UTF-8")
        }
        val url = URL("http://$host:$uploadPort/api/upload?$q")
        val conn = url.openConnection() as HttpURLConnection
        return try {
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.connectTimeout = 4000
            conn.readTimeout = 30000
            conn.setRequestProperty("Content-Type", "video/mp4")
            conn.setFixedLengthStreamingMode(file.length())
            conn.outputStream.use { os -> file.inputStream().use { it.copyTo(os, 1 shl 16) } }
            conn.responseCode == 200
        } catch (e: Exception) {
            Log.w(TAG, "Upload ${file.name}: ${e.message}")
            false
        } finally {
            conn.disconnect()
        }
    }

    private fun sleep(ms: Long) = try { Thread.sleep(ms) } catch (_: InterruptedException) {}
}
