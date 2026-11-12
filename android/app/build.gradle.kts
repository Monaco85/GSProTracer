plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Versionsnummern kommen aus der CI (GitHub Actions): -PversionCode=… -PversionName=…
// versionCode = GitHub-Laufnummer -> steigt mit jedem Build, die App erkennt Updates daran.
val ciVersionCode = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
val ciVersionName = "1.0.$ciVersionCode"

// Fester Signaturschluessel, damit sich neue Versionen ueber die alte installieren lassen.
// Wird in der CI aus GitHub-Secrets befuellt (siehe .github/workflows/android.yml).
val keystorePath = System.getenv("TRACER_KEYSTORE")
val hasReleaseKey = !keystorePath.isNullOrBlank() && file(keystorePath).exists()

android {
    namespace = "de.gsprotracer.cam"
    compileSdk = 35

    defaultConfig {
        applicationId = "de.gsprotracer.cam"
        minSdk = 29
        targetSdk = 35
        versionCode = ciVersionCode
        versionName = ciVersionName
    }

    signingConfigs {
        if (hasReleaseKey) {
            create("release") {
                storeFile = file(keystorePath!!)
                storeType = "pkcs12"
                storePassword = System.getenv("TRACER_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("TRACER_KEY_ALIAS") ?: "tracer"
                keyPassword = System.getenv("TRACER_KEY_PASSWORD") ?: System.getenv("TRACER_KEYSTORE_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (hasReleaseKey) signingConfigs.getByName("release") else signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    // Kamera/Netz nur mit Android-Framework; core nur fuer FileProvider (In-App-Update)
    implementation("androidx.core:core-ktx:1.13.1")
}
