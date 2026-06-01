import org.gradle.api.tasks.Sync

plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

val generatedPythonDir = layout.buildDirectory.dir("generated/python/main")

val syncPythonBackend by tasks.registering(Sync::class) {
    from(rootProject.projectDir.parentFile) {
        include("web_server.py")
        include("web_translate_client.py")
        include("static/**")
    }
    into(generatedPythonDir)
}

android {
    namespace = "com.febilly.qwenlivetranslate"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.febilly.qwenlivetranslate"
        minSdk = 24
        targetSdk = 35
        versionCode = 3
        versionName = "1.2"

        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }
}

chaquopy {
    defaultConfig {
        version = "3.12"
        pip {
            install("-r", file("../../requirements.txt").absolutePath)
        }
    }
    sourceSets {
        getByName("main") {
            srcDir(generatedPythonDir)
        }
    }
}

tasks.named("preBuild") {
    dependsOn(syncPythonBackend)
}

tasks.matching { it.name.matches(Regex("merge.*PythonSources")) }.configureEach {
    dependsOn(syncPythonBackend)
}
