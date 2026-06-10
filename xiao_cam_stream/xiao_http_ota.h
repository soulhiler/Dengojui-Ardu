#pragma once
/** HTTP POST /update?pwd=…&size=N — прошивка по Wi‑Fi (когда USB/COM мёртв). */

#include <Arduino.h>
#include <Update.h>
#include <WebServer.h>

#ifndef XIAO_OTA_PASSWORD
#define XIAO_OTA_PASSWORD ""
#endif
#ifndef XIAO_OTA_ENABLE
#define XIAO_OTA_ENABLE 0
#endif

static bool gHttpOtaAuthed = false;

static inline bool xiaoHttpOtaPwdOk(WebServer &srv) {
#if !XIAO_OTA_ENABLE
  return false;
#else
  return srv.hasArg("pwd") && srv.arg("pwd") == F(XIAO_OTA_PASSWORD);
#endif
}

static void xiaoHttpOtaFinish(WebServer &srv) {
  srv.sendHeader(F("Connection"), F("close"));
  if (!gHttpOtaAuthed) {
    srv.send(401, F("text/plain; charset=utf-8"), F("unauthorized"));
    return;
  }
  if (Update.hasError()) {
    srv.send(502, F("text/plain; charset=utf-8"), Update.errorString());
  } else {
    srv.send(200, F("text/plain; charset=utf-8"), F("ok reboot"));
    delay(400);
    ESP.restart();
  }
}

static void xiaoHttpOtaUpload(WebServer &srv) {
  HTTPUpload &up = srv.upload();
  if (up.status == UPLOAD_FILE_START) {
    gHttpOtaAuthed = xiaoHttpOtaPwdOk(srv);
    if (!gHttpOtaAuthed) {
      Serial.println(F("http ota: bad pwd"));
      return;
    }
    size_t sz = UPDATE_SIZE_UNKNOWN;
    if (srv.hasArg("size")) {
      sz = static_cast<size_t>(srv.arg("size").toInt());
    }
    Serial.printf("http ota: begin %s size=%lu\n", up.filename.c_str(), static_cast<unsigned long>(sz));
    if (!Update.begin(sz, U_FLASH)) {
      gHttpOtaAuthed = false;
      Update.printError(Serial);
    }
  } else if (gHttpOtaAuthed && up.status == UPLOAD_FILE_WRITE) {
    if (Update.write(up.buf, up.currentSize) != up.currentSize) {
      Update.printError(Serial);
      gHttpOtaAuthed = false;
    }
  } else if (gHttpOtaAuthed && up.status == UPLOAD_FILE_END) {
    if (!Update.end(true)) {
      Update.printError(Serial);
      gHttpOtaAuthed = false;
    } else {
      Serial.printf("http ota: done %lu bytes\n", static_cast<unsigned long>(up.totalSize));
    }
  }
}

extern WebServer server;

static void xiaoHttpOtaOnFinish() {
  xiaoHttpOtaFinish(server);
}

static void xiaoHttpOtaOnUpload() {
  xiaoHttpOtaUpload(server);
}

static inline void xiaoHttpOtaRegister() {
#if XIAO_OTA_ENABLE
  server.on(F("/update"), HTTP_POST, xiaoHttpOtaOnFinish, xiaoHttpOtaOnUpload);
  Serial.println(F("HTTP OTA: POST /update?pwd=***&size=bytes (multipart firmware)"));
#endif
}
