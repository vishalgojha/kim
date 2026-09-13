plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android { namespace = "com.vishal.kim"; compileSdk = 35
    defaultConfig { applicationId = "com.vishal.kim"; minSdk = 26; targetSdk = 35; versionCode = 1; versionName = "0.1.0" }
}

kotlin { jvmToolchain(17) }

dependencies {
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
}
