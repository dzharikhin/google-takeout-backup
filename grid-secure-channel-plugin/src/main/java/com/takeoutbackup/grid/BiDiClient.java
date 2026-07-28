package com.takeoutbackup.grid;

import com.google.gson.JsonObject;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.net.http.WebSocket.Listener;
import java.nio.ByteBuffer;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.logging.Level;
import java.util.logging.Logger;

public class BiDiClient {

    private static final Logger LOGGER = Logger.getLogger(BiDiClient.class.getName());
    private static final int COMMAND_TIMEOUT_SECONDS = 10;

    private final String webSocketUrl;
    private final HttpClient http;
    private volatile WebSocket ws;
    private final AtomicInteger nextId = new AtomicInteger(1);
    private final ConcurrentMap<Integer, CompletableFuture<JsonObject>> pending = new ConcurrentHashMap<>();
    private final StringBuilder fragBuf = new StringBuilder();

    public BiDiClient(String webSocketUrl) {
        this.webSocketUrl = webSocketUrl;
        this.http = HttpClient.newHttpClient();
    }

    public synchronized void ensureSession() throws Exception {
        if (ws != null) {
            return;
        }
        URI uri = URI.create(webSocketUrl);
        fragBuf.setLength(0);
        ws = http.newWebSocketBuilder()
                .buildAsync(uri, new BidiListener())
                .join();
    }

    public CompletableFuture<JsonObject> sendCommand(String method, JsonObject params) throws Exception {
        int id = nextId.getAndIncrement();
        CompletableFuture<JsonObject> future = new CompletableFuture<>();
        pending.put(id, future);

        JsonObject command = new JsonObject();
        command.addProperty("id", id);
        command.addProperty("method", method);
        command.add("params", params);

        synchronized (fragBuf) {
            ws.sendText(command.toString(), true);
        }

        return future.orTimeout(COMMAND_TIMEOUT_SECONDS, java.util.concurrent.TimeUnit.SECONDS)
            .exceptionally(e -> {
                pending.remove(id);
                if (e instanceof java.util.concurrent.TimeoutException) {
                    LOGGER.log(Level.FINE, "BiDi command timeout: {0}", method);
                    throw new RuntimeException("BiDi command timeout", e);
                } else {
                    LOGGER.log(Level.WARNING, "BiDi command failed for " + method + ": " + e.getMessage());
                    throw new RuntimeException("BiDi command failed", e);
                }
            });
    }

    public synchronized void close() {
        if (ws != null) {
            try {
                try {
                    sendCommand("session.end", new JsonObject()).get(1, java.util.concurrent.TimeUnit.SECONDS);
                } catch (Exception e) {
                    LOGGER.log(Level.FINE, "session.end failed: {0}", e.getMessage());
                }
            } finally {
                ws.sendClose(WebSocket.NORMAL_CLOSURE, "shutdown");
                ws = null;
            }
        }
        for (CompletableFuture<JsonObject> f : pending.values()) {
            f.completeExceptionally(new RuntimeException("BiDiClient closed"));
        }
        pending.clear();
        fragBuf.setLength(0);
    }

    private class BidiListener implements Listener {
        @Override
        public void onOpen(WebSocket webSocket) {
            Listener.super.onOpen(webSocket);
        }

        @Override
        public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
            synchronized (fragBuf) {
                fragBuf.append(data);
                if (last) {
                    String full = fragBuf.toString();
                    fragBuf.setLength(0);
                    try {
                        JsonObject msg = com.google.gson.JsonParser.parseString(full).getAsJsonObject();
                        if (msg.has("id")) {
                            int id = msg.get("id").getAsInt();
                            CompletableFuture<JsonObject> future = pending.remove(id);
                            if (future != null) {
                                if (msg.has("result")) {
                                    future.complete(msg.getAsJsonObject("result"));
                                } else if (msg.has("error")) {
                                    String error = msg.get("error").toString();
                                    String message = msg.has("message") ? " — " + msg.get("message").toString() : "";
                                    future.completeExceptionally(new RuntimeException("BiDi error: " + error + message));
                                } else {
                                    future.completeExceptionally(new RuntimeException("Unexpected BiDi response: " + full));
                                }
                            }
                        } else {
                            LOGGER.log(Level.FINE, "BiDi event ignored (no id): {0}", msg.get("method"));
                        }
                    } catch (Exception e) {
                        LOGGER.log(Level.FINE, "BiDi message parse failed: {0}", e.getMessage());
                    }
                }
            }
            return Listener.super.onText(webSocket, data, last);
        }

        @Override
        public void onError(WebSocket webSocket, Throwable error) {
            LOGGER.log(Level.FINE, "BiDi WebSocket error: {0}", error.getMessage());
        }

        @Override
        public CompletionStage<?> onClose(WebSocket webSocket, int statusCode, String reason) {
            LOGGER.log(Level.FINE, "BiDi WebSocket closed: " + statusCode + " " + reason);
            ws = null;
            for (CompletableFuture<JsonObject> f : pending.values()) {
                f.completeExceptionally(new RuntimeException("BiDi connection closed"));
            }
            pending.clear();
            fragBuf.setLength(0);
            return Listener.super.onClose(webSocket, statusCode, reason);
        }
    }
}