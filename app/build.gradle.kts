plugins { id("com.android.application"); id("org.jetbrains.kotlin.android") }

android { namespace = "com.kunguglobal.velocity1"; compileSdk = 36
    defaultConfig { applicationId = "com.kunguglobal.velocity1"; minSdk = 26; targetSdk = 36; versionCode = 1; versionName = "1.0.0" }
}

dependencies { implementation("androidx.core:core-ktx:1.17.0"); implementation("androidx.appcompat:appcompat:1.7.1"); implementation("com.google.android.material:material:1.13.0") }
