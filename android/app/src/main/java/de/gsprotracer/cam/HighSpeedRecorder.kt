package de.gsprotracer.cam

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.SurfaceTexture
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraConstrainedHighSpeedCaptureSession
import android.hardware.camera2.CameraDevice
import android.hardware.camera2.CameraManager
import android.hardware.camera2.CaptureRequest
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.MediaMuxer
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.util.Log
import android.util.Range
import android.util.Size
import android.view.Surface
import java.io.File
import java.nio.ByteBuffer
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Nimmt dauerhaft 720p mit (wenn moeglich) 240 fps auf und haelt die letzten
 * [BUFFER_SECONDS] Sekunden als fertig kodiertes H.264 im Speicher (Ringpuffer).
 * Bei einem Trigger wird ein Ausschnitt rueckwirkend als MP4 geschrieben.
 *
 * Warum Ringpuffer? Der Garmin R10 meldet die Schlagdaten erst NACH dem Treffmoment.
 * Ein Video, das erst dann startet, wuerde den Schwung verpassen.
 */
class HighSpeedRecorder(private val context: Context, private val listener: Listener) {

    interface Listener {
        fun onCameraStatus(text: String)
        fun onCameraError(text: String)
        fun onClipReady(clip: ClipInfo)
        fun onClipFailed(shotId: String, reason: String)
    }

    data class Mode(
        val cameraId: String,
        val size: Size,
        val fps: Int,
        val highSpeed: Boolean,
        val fpsRange: Range<Int>,
        val sensorOrientation: Int,
    )

    data class ClipInfo(
        val file: File,
        val shotId: String,
        val fps: Int,
        val width: Int,
        val height: Int,
        val durationSec: Double,
        val triggerTimeSec: Double,
        val delaySec: Double,
    )

    private class Sample(val data: ByteArray, val ptsUs: Long, val key: Boolean)

    companion object {
        private const val TAG = "HighSpeedRecorder"
        const val TARGET_FPS = 240
        val TARGET_SIZE = Size(1280, 720)
        val HS_SIZES = listOf(Size(1280, 720), Size(1920, 1080))
        const val BUFFER_SECONDS = 12.0
    }

    private val manager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
    private val camThread = HandlerThread("camera").apply { start() }
    private val camHandler = Handler(camThread.looper)
    private val codecThread = HandlerThread("encoder").apply { start() }
    private val codecHandler = Handler(codecThread.looper)
    private val clipExecutor = Executors.newSingleThreadScheduledExecutor()

    private val ring = ArrayDeque<Sample>()
    private val ringLock = Any()

    @Volatile var mode: Mode? = null; private set
    @Volatile var measuredFps = 0.0; private set
    @Volatile var cameraInfo = ""; private set
    /** Zusaetzliche Drehung des Videos (0/90/180/270), z.B. wenn das Handy kopfueber montiert ist. */
    @Volatile var extraRotation = 0
    @Volatile private var outputFormat: MediaFormat? = null
    /** min(elapsedRealtime - pts) -> Abbildung Wanduhr <-> Frame-Zeitstempel. */
    @Volatile private var clockOffsetUs = Long.MAX_VALUE

    private var camera: CameraDevice? = null
    private var session: CameraCaptureSession? = null
    private var encoder: MediaCodec? = null
    private var encoderSurface: Surface? = null
    private var previewSurface: Surface? = null
    private var locked = false

    private var fpsWindowStart = 0L
    private var fpsWindowFrames = 0

    // ------------------------------------------------------------------ Modus-Auswahl

    fun describeCameras(): String {
        val sb = StringBuilder()
        for (id in manager.cameraIdList) {
            val ch = manager.getCameraCharacteristics(id)
            val facing = when (ch.get(CameraCharacteristics.LENS_FACING)) {
                CameraCharacteristics.LENS_FACING_BACK -> "hinten"
                CameraCharacteristics.LENS_FACING_FRONT -> "vorne"
                else -> "extern"
            }
            val caps = ch.get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES) ?: IntArray(0)
            val map = ch.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
            sb.append("Kamera $id ($facing)")
            if (caps.contains(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_CONSTRAINED_HIGH_SPEED_VIDEO) && map != null) {
                val hs = map.highSpeedVideoSizes.joinToString(", ") { s ->
                    val r = map.getHighSpeedVideoFpsRangesFor(s).filter { it.lower == it.upper }
                        .map { it.upper }.distinct().sorted()
                    "${s.width}x${s.height}@${r.joinToString("/")}"
                }
                sb.append(": High-Speed $hs")
            } else {
                val r = ch.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES)
                    ?.maxOfOrNull { it.upper } ?: 0
                sb.append(": kein High-Speed, max $r fps")
            }
            sb.append('\n')
        }
        return sb.toString().trim()
    }

    /**
     * Reihenfolge: 720p@240 > 1080p@240 > 720p@120 > 1080p@120 > normale Session (30 fps).
     * Das Poco X3 bietet Fremd-Apps laut Erfahrung aus "Tennisrecord" sicher 120 fps in 1080p an,
     * 60 fps gibt es dort nicht. Ob 240 fps freigegeben ist, zeigt erst das Geraet.
     */
    private fun findMode(): Mode? {
        var best: Mode? = null
        fun score(m: Mode) = (if (m.highSpeed) 100_000 else 0) + m.fps * 10 + (if (m.size == TARGET_SIZE) 1 else 0)
        for (id in manager.cameraIdList) {
            val ch = manager.getCameraCharacteristics(id)
            if (ch.get(CameraCharacteristics.LENS_FACING) != CameraCharacteristics.LENS_FACING_BACK) continue
            val map = ch.get(CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP) ?: continue
            val caps = ch.get(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES) ?: IntArray(0)
            val orient = ch.get(CameraCharacteristics.SENSOR_ORIENTATION) ?: 90

            if (caps.contains(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES_CONSTRAINED_HIGH_SPEED_VIDEO)) {
                for (size in HS_SIZES) {
                    if (!map.highSpeedVideoSizes.contains(size)) continue
                    val r = map.getHighSpeedVideoFpsRangesFor(size)
                        .filter { it.lower == it.upper && it.upper <= TARGET_FPS }
                        .maxByOrNull { it.upper } ?: continue
                    val m = Mode(id, size, r.upper, true, r, orient)
                    if (best == null || score(m) > score(best)) best = m
                }
            }
            // Fallback: normale Session (meist 30/60 fps)
            val sizes = map.getOutputSizes(SurfaceTexture::class.java) ?: emptyArray()
            if (sizes.contains(TARGET_SIZE)) {
                val ranges = ch.get(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES) ?: emptyArray()
                val r = ranges.filter { it.lower == it.upper }.maxByOrNull { it.upper }
                    ?: ranges.maxByOrNull { it.upper }
                if (r != null) {
                    val m = Mode(id, TARGET_SIZE, r.upper, false, r, orient)
                    if (best == null || score(m) > score(best)) best = m
                }
            }
        }
        return best
    }

    // ------------------------------------------------------------------ Start/Stop

    @SuppressLint("MissingPermission")
    fun start(previewTexture: SurfaceTexture?) {
        camHandler.post {
            try {
                cameraInfo = describeCameras()
                Log.i(TAG, cameraInfo)
                val m = findMode() ?: run {
                    listener.onCameraError("Keine Rueckkamera mit 1280x720 gefunden.\n$cameraInfo")
                    return@post
                }
                mode = m
                clockOffsetUs = Long.MAX_VALUE
                fpsWindowStart = 0L
                fpsWindowFrames = 0
                synchronized(ringLock) { ring.clear() }
                outputFormat = null

                previewSurface = previewTexture?.let {
                    it.setDefaultBufferSize(m.size.width, m.size.height)
                    Surface(it)
                }
                setupEncoder(m)
                listener.onCameraStatus(
                    "Kamera ${m.cameraId}: ${m.size.width}x${m.size.height} @ ${m.fps} fps " +
                        if (m.highSpeed) "(High-Speed)" else "(KEIN High-Speed verfuegbar!)"
                )
                manager.openCamera(m.cameraId, object : CameraDevice.StateCallback() {
                    override fun onOpened(device: CameraDevice) {
                        camera = device
                        createSession(device, m)
                    }

                    override fun onDisconnected(device: CameraDevice) {
                        device.close(); camera = null
                        listener.onCameraError("Kamera getrennt (andere App?)")
                    }

                    override fun onError(device: CameraDevice, error: Int) {
                        device.close(); camera = null
                        listener.onCameraError("Kamerafehler $error")
                    }
                }, camHandler)
            } catch (e: Exception) {
                Log.e(TAG, "start", e)
                listener.onCameraError("Start fehlgeschlagen: ${e.message}")
            }
        }
    }

    fun stop() {
        camHandler.post {
            try { session?.close() } catch (_: Exception) {}
            session = null
            try { camera?.close() } catch (_: Exception) {}
            camera = null
            try { encoder?.stop() } catch (_: Exception) {}
            try { encoder?.release() } catch (_: Exception) {}
            encoder = null
            encoderSurface?.release(); encoderSurface = null
            previewSurface?.release(); previewSurface = null
            mode = null
            measuredFps = 0.0
        }
    }

    fun release() {
        stop()
        camThread.quitSafely()
        codecThread.quitSafely()
        clipExecutor.shutdown()
    }

    private fun setupEncoder(m: Mode) {
        val bitrate = when {
            m.fps >= 200 -> 40_000_000
            m.fps >= 100 -> if (m.size.width > 1280) 30_000_000 else 25_000_000
            else -> 16_000_000
        }
        var lastError: Exception? = null
        for (withOperatingRate in listOf(true, false)) {
            val codec = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
            val f = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, m.size.width, m.size.height).apply {
                setInteger(MediaFormat.KEY_COLOR_FORMAT, MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
                setInteger(MediaFormat.KEY_BIT_RATE, bitrate)
                setInteger(MediaFormat.KEY_FRAME_RATE, m.fps)
                setFloat(MediaFormat.KEY_I_FRAME_INTERVAL, 0.5f) // viele Keyframes -> praeziser Clip-Start
                setInteger(MediaFormat.KEY_MAX_B_FRAMES, 0)
                if (withOperatingRate) {
                    setInteger(MediaFormat.KEY_OPERATING_RATE, m.fps)
                    setInteger(MediaFormat.KEY_PRIORITY, 0)
                }
            }
            try {
                codec.setCallback(encoderCallback, codecHandler)
                codec.configure(f, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
                encoderSurface = codec.createInputSurface()
                codec.start()
                encoder = codec
                return
            } catch (e: Exception) {
                Log.w(TAG, "Encoder-Konfiguration fehlgeschlagen (operatingRate=$withOperatingRate)", e)
                lastError = e
                codec.release()
            }
        }
        throw IllegalStateException("H.264-Encoder laesst sich nicht konfigurieren: ${lastError?.message}")
    }

    /** Gleiche API wie in Tennisrecord (auf dem Poco X3 erprobt). */
    @Suppress("DEPRECATION")
    private fun createSession(device: CameraDevice, m: Mode) {
        val surfaces = listOfNotNull(previewSurface, encoderSurface)
        val cb = object : CameraCaptureSession.StateCallback() {
            override fun onConfigured(s: CameraCaptureSession) {
                session = s
                applyRepeating()
            }

            override fun onConfigureFailed(s: CameraCaptureSession) {
                listener.onCameraError("Kamera-Session abgelehnt (${m.size.width}x${m.size.height} @ ${m.fps} fps)")
            }
        }
        if (m.highSpeed) device.createConstrainedHighSpeedCaptureSession(surfaces, cb, camHandler)
        else device.createCaptureSession(surfaces, cb, camHandler)
    }

    /** AE/AWB/AF einfrieren, damit nichts pumpt, wenn der Golfer ins Bild kommt. */
    fun setLocked(lock: Boolean) {
        camHandler.post { locked = lock; applyRepeating() }
    }

    private fun applyRepeating() {
        val s = session ?: return
        val dev = camera ?: return
        val m = mode ?: return
        try {
            val b = dev.createCaptureRequest(CameraDevice.TEMPLATE_RECORD)
            previewSurface?.let { b.addTarget(it) }
            encoderSurface?.let { b.addTarget(it) }
            b.set(CaptureRequest.CONTROL_MODE, CaptureRequest.CONTROL_MODE_AUTO)
            b.set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, m.fpsRange)
            b.set(CaptureRequest.CONTROL_AE_LOCK, locked)
            b.set(CaptureRequest.CONTROL_AWB_LOCK, locked)
            b.set(
                CaptureRequest.CONTROL_AF_MODE,
                if (locked) CaptureRequest.CONTROL_AF_MODE_OFF else CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_VIDEO
            )
            s.stopRepeating()
            if (m.highSpeed && s is CameraConstrainedHighSpeedCaptureSession) {
                s.setRepeatingBurst(s.createHighSpeedRequestList(b.build()), null, camHandler)
            } else {
                s.setRepeatingRequest(b.build(), null, camHandler)
            }
        } catch (e: Exception) {
            Log.e(TAG, "applyRepeating", e)
            listener.onCameraError("Aufnahme-Start fehlgeschlagen: ${e.message}")
        }
    }

    // ------------------------------------------------------------------ Encoder -> Ringpuffer

    private val encoderCallback = object : MediaCodec.Callback() {
        override fun onInputBufferAvailable(codec: MediaCodec, index: Int) {}

        override fun onOutputFormatChanged(codec: MediaCodec, format: MediaFormat) {
            outputFormat = format
        }

        override fun onError(codec: MediaCodec, e: MediaCodec.CodecException) {
            Log.e(TAG, "Encoder-Fehler", e)
            listener.onCameraError("Encoder-Fehler: ${e.diagnosticInfo}")
        }

        override fun onOutputBufferAvailable(codec: MediaCodec, index: Int, info: MediaCodec.BufferInfo) {
            val buf: ByteBuffer? = try { codec.getOutputBuffer(index) } catch (_: Exception) { null }
            if (buf == null || info.size == 0 || (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0) {
                try { codec.releaseOutputBuffer(index, false) } catch (_: Exception) {}
                return
            }
            val data = ByteArray(info.size)
            buf.position(info.offset)
            buf.limit(info.offset + info.size)
            buf.get(data)
            val pts = info.presentationTimeUs
            val key = (info.flags and MediaCodec.BUFFER_FLAG_KEY_FRAME) != 0
            try { codec.releaseOutputBuffer(index, false) } catch (_: Exception) {}

            val nowUs = SystemClock.elapsedRealtimeNanos() / 1000
            val off = nowUs - pts
            if (off < clockOffsetUs) clockOffsetUs = off

            synchronized(ringLock) {
                ring.addLast(Sample(data, pts, key))
                val minPts = pts - (BUFFER_SECONDS * 1_000_000).toLong()
                while (ring.isNotEmpty() && ring.first().ptsUs < minPts) ring.removeFirst()
            }

            fpsWindowFrames++
            if (fpsWindowStart == 0L) fpsWindowStart = nowUs
            val dt = nowUs - fpsWindowStart
            if (dt >= 1_000_000) {
                measuredFps = fpsWindowFrames * 1_000_000.0 / dt
                fpsWindowFrames = 0
                fpsWindowStart = nowUs
            }
        }
    }

    fun bufferedSeconds(): Double = synchronized(ringLock) {
        if (ring.size < 2) 0.0 else (ring.last().ptsUs - ring.first().ptsUs) / 1e6
    }

    // ------------------------------------------------------------------ Clip schneiden

    /**
     * @param pre   Sekunden vor dem (geschaetzten) Impact
     * @param post  Sekunden nach dem Impact
     * @param delay Latenz R10: Impact liegt [delay] Sekunden VOR dem Eintreffen des Triggers
     */
    fun requestClip(shotId: String, pre: Double, post: Double, delay: Double, outDir: File) {
        val off = clockOffsetUs
        if (off == Long.MAX_VALUE || outputFormat == null) {
            listener.onClipFailed(shotId, "Kamera laeuft noch nicht")
            return
        }
        val nowUs = SystemClock.elapsedRealtimeNanos() / 1000
        val trigPts = nowUs - off
        val center = trigPts - (delay * 1e6).toLong()
        val startPts = center - (pre * 1e6).toLong()
        val endPts = center + (post * 1e6).toLong()
        // warten bis alle benoetigten Frames kodiert sind (+ Encoder-Latenz)
        val waitMs = ((endPts - trigPts).coerceAtLeast(0) / 1000) + 400
        clipExecutor.schedule({
            try {
                writeClip(shotId, startPts, endPts, trigPts, delay, outDir)
            } catch (e: Exception) {
                Log.e(TAG, "writeClip", e)
                listener.onClipFailed(shotId, e.message ?: "Fehler beim Schreiben")
            }
        }, waitMs, TimeUnit.MILLISECONDS)
    }

    private fun writeClip(shotId: String, startPts: Long, endPts: Long, trigPts: Long, delay: Double, outDir: File) {
        val m = mode ?: throw IllegalStateException("Kamera gestoppt")
        val fmt = outputFormat ?: throw IllegalStateException("kein Videoformat")
        val samples: List<Sample> = synchronized(ringLock) {
            val all = ring.toList()
            if (all.isEmpty()) throw IllegalStateException("Puffer leer")
            var startIdx = all.indexOfLast { it.key && it.ptsUs <= startPts }
            if (startIdx < 0) startIdx = all.indexOfFirst { it.key }
            if (startIdx < 0) throw IllegalStateException("kein Keyframe im Puffer")
            all.subList(startIdx, all.size).filter { it.ptsUs <= endPts }
        }
        if (samples.size < 10) throw IllegalStateException("zu wenige Frames (${samples.size})")

        outDir.mkdirs()
        val tmp = File(outDir, "$shotId.mp4.tmp")
        val muxer = MediaMuxer(tmp.path, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        try {
            muxer.setOrientationHint(((m.sensorOrientation - 90 + extraRotation) % 360 + 360) % 360)
            val track = muxer.addTrack(fmt)
            muxer.start()
            val first = samples.first().ptsUs
            val maxSize = samples.maxOf { it.data.size }
            val bb = ByteBuffer.allocateDirect(maxSize)
            val info = MediaCodec.BufferInfo()
            var lastPts = -1L
            for (s in samples) {
                val pts = s.ptsUs - first
                if (pts <= lastPts) continue // Muxer verlangt streng steigende Zeitstempel
                lastPts = pts
                bb.clear(); bb.put(s.data); bb.flip()
                info.set(0, s.data.size, pts, if (s.key) MediaCodec.BUFFER_FLAG_KEY_FRAME else 0)
                muxer.writeSampleData(track, bb, info)
            }
            muxer.stop()
        } finally {
            try { muxer.release() } catch (_: Exception) {}
        }
        val file = File(outDir, "$shotId.mp4")
        if (!tmp.renameTo(file)) throw IllegalStateException("Umbenennen fehlgeschlagen")
        val first = samples.first().ptsUs
        val rotated = (m.sensorOrientation - 90 + extraRotation) % 180 != 0
        listener.onClipReady(
            ClipInfo(
                file = file,
                shotId = shotId,
                fps = m.fps,
                width = if (rotated) m.size.height else m.size.width,
                height = if (rotated) m.size.width else m.size.height,
                durationSec = (samples.last().ptsUs - first) / 1e6,
                triggerTimeSec = (trigPts - first) / 1e6,
                delaySec = delay,
            )
        )
    }
}
