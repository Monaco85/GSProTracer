package de.gsprotracer.cam

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Holt den neuesten Build aus dem GitHub-Release "cam-latest" und startet die Installation
 * (gleiches Vorgehen wie in Tennisrecord). Der Token ist optional, solange das Repository oeffentlich ist;
 * bei einem privaten Repository: Fine-grained Token, nur dieses Repo, "Contents: Read-only".
 */
class Updater(private val context: Context) {

    data class Release(val build: Long, val name: String, val apiUrl: String, val sizeBytes: Long)

    private val prefs = context.getSharedPreferences("settings", Context.MODE_PRIVATE)

    var token: String
        get() = prefs.getString("ghToken", "") ?: ""
        set(value) = prefs.edit().putString("ghToken", value.trim()).apply()

    fun installedBuild(): Long =
        context.packageManager.getPackageInfo(context.packageName, 0).longVersionCode

    fun installedName(): String =
        context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "?"

    /** Neuester veroeffentlichter Build. Wirft eine Exception mit lesbarer Meldung. */
    fun latest(): Release {
        val c = open(RELEASE_URL, "application/vnd.github+json", withAuth = token.isNotEmpty())
        try {
            when (val code = c.responseCode) {
                200 -> Unit
                401, 403 -> throw IllegalStateException("Token ungültig oder GitHub-Limit erreicht (HTTP $code)")
                404 -> throw IllegalStateException("Noch kein Build veröffentlicht (oder Repo privat und kein Token)")
                else -> throw IllegalStateException("GitHub antwortet mit HTTP $code")
            }
            val json = JSONObject(c.inputStream.bufferedReader().use { it.readText() })
            val assets = json.getJSONArray("assets")
            for (i in 0 until assets.length()) {
                val a = assets.getJSONObject(i)
                val name = a.getString("name")
                val build = Regex("v(\\d+)\\.apk$").find(name)?.groupValues?.get(1)?.toLongOrNull()
                if (build != null) return Release(build, name, a.getString("url"), a.optLong("size"))
            }
            throw IllegalStateException("Im Release liegt keine passende APK")
        } finally {
            c.disconnect()
        }
    }

    /** Laedt die APK in den Cache. GitHub leitet auf einen signierten Speicher-Link um; dorthin darf der Token nicht mit. */
    fun download(r: Release, onProgress: (Int) -> Unit): File {
        val dir = File(context.cacheDir, "updates").also { it.mkdirs() }
        dir.listFiles()?.forEach { it.delete() }
        val file = File(dir, "gsprotracer-update.apk")

        var url = r.apiUrl
        var auth = token.isNotEmpty()
        for (hop in 0 until 6) {
            val c = open(url, "application/octet-stream", withAuth = auth)
            c.instanceFollowRedirects = false
            try {
                val code = c.responseCode
                if (code in 300..399) {
                    url = c.getHeaderField("Location") ?: throw IllegalStateException("Umleitung ohne Ziel")
                    auth = false
                    continue
                }
                if (code != 200) throw IllegalStateException("Download fehlgeschlagen (HTTP $code)")
                val total = if (c.contentLengthLong > 0) c.contentLengthLong else r.sizeBytes
                c.inputStream.use { input ->
                    file.outputStream().use { out ->
                        val buf = ByteArray(64 * 1024)
                        var done = 0L
                        var last = -1
                        while (true) {
                            val n = input.read(buf)
                            if (n < 0) break
                            out.write(buf, 0, n)
                            done += n
                            if (total > 0) {
                                val pct = (done * 100 / total).toInt()
                                if (pct != last) { last = pct; onProgress(pct) }
                            }
                        }
                    }
                }
                return file
            } finally {
                c.disconnect()
            }
        }
        throw IllegalStateException("Zu viele Umleitungen")
    }

    /** Startet den Android-Installer. false = zuerst Erlaubnis "Unbekannte Apps installieren" noetig. */
    fun install(activity: Activity, file: File): Boolean {
        if (!activity.packageManager.canRequestPackageInstalls()) {
            activity.startActivity(
                Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${activity.packageName}"))
            )
            return false
        }
        val uri = FileProvider.getUriForFile(activity, "${activity.packageName}.fileprovider", file)
        activity.startActivity(
            Intent(Intent.ACTION_VIEW)
                .setDataAndType(uri, "application/vnd.android.package-archive")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        )
        return true
    }

    private fun open(url: String, accept: String, withAuth: Boolean): HttpURLConnection {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 15_000
        c.readTimeout = 30_000
        c.setRequestProperty("Accept", accept)
        c.setRequestProperty("User-Agent", "GSProTracerCam")
        if (withAuth) c.setRequestProperty("Authorization", "Bearer $token")
        return c
    }

    companion object {
        const val RELEASE_URL = "https://api.github.com/repos/Monaco85/GSProTracer/releases/tags/cam-latest"
    }
}
