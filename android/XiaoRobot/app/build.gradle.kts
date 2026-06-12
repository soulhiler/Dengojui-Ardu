plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Единый источник версии приложения: dist/app-version.txt в корне репозитория.
// Это же число отдаёт дашборд (GET /app/version.json) — приложение сравнивает
// с собой и предлагает обновление. Релиз = +1 в этом файле.
val appVersionFile = rootProject.file("../../dist/app-version.txt")
val appVersionCode: Int =
    if (appVersionFile.exists()) appVersionFile.readText().trim().toInt() else 1

android {
    namespace = "com.denzhogzhuy.xiaorobot"
    compileSdk = 35
    defaultConfig {
        applicationId = "com.denzhogzhuy.xiaorobot"
        minSdk = 26
        targetSdk = 35
        versionCode = appVersionCode
        versionName = "1.$appVersionCode"
    }
    // Общий keystore в репозитории: иначе CI подписывает каждый билд новым
    // случайным debug-ключом и Android отказывается ставить обновление поверх.
    signingConfigs {
        create("shared") {
            storeFile = rootProject.file("debug.keystore")
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
    }
    buildTypes {
        debug {
            signingConfig = signingConfigs.getByName("shared")
        }
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("shared")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        viewBinding = true
    }
    // Имя APK с версией (например xiao-robot-v5-debug.apk) — чтобы в dist/ и в
    // release apk-latest было видно, какая это сборка. Версия — dist/app-version.txt.
    applicationVariants.all {
        val variantName = buildType.name
        outputs.all {
            (this as com.android.build.gradle.internal.api.BaseVariantOutputImpl).outputFileName =
                "xiao-robot-v$appVersionCode-$variantName.apk"
        }
    }
}
dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.2.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
}
