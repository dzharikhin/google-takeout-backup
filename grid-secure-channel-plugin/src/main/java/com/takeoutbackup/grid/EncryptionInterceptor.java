package com.takeoutbackup.grid;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.JsonPrimitive;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Optional;
import java.util.concurrent.Callable;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;
import java.util.logging.Level;
import java.util.logging.Logger;
import org.bouncycastle.util.encoders.Hex;
import org.jspecify.annotations.Nullable;
import org.openqa.selenium.Capabilities;
import org.openqa.selenium.events.EventBus;
import org.openqa.selenium.grid.config.Config;
import org.openqa.selenium.grid.data.SessionCreatedData;
import org.openqa.selenium.grid.data.SessionCreatedEvent;
import org.openqa.selenium.grid.data.SessionClosedData;
import org.openqa.selenium.grid.data.SessionClosedEvent;
import org.openqa.selenium.grid.node.NodeCommandInterceptor;
import org.openqa.selenium.remote.SessionId;
import org.openqa.selenium.remote.http.Contents;
import org.openqa.selenium.remote.http.HttpRequest;
import org.openqa.selenium.remote.http.HttpResponse;
import ecies.Ecies;
import ecies.common.ECKeyPair;

public class EncryptionInterceptor implements NodeCommandInterceptor {

    private byte[] publicKeyBytes;
    private byte[] privateKeyBytes;
    private static final Logger LOGGER = Logger.getLogger(EncryptionInterceptor.class.getName());
    private final ConcurrentMap<SessionId, String> sessionBidiUrls = new ConcurrentHashMap<>();
    private final ConcurrentMap<SessionId, BiDiClient> bidiClients = new ConcurrentHashMap<>();

    @Override
    public boolean isEnabled(Config config) {
        return true;
    }

    @Override
    public void initialize(Config config, EventBus bus) {
        Optional<String> pubOpt = config.get("encryption", "public-key");
        Optional<String> privOpt = config.get("encryption", "private-key");

        try {
            String publicKeyHex;
            String privateKeyHex;

            if (pubOpt.isPresent() && privOpt.isPresent()) {
                publicKeyHex = pubOpt.get();
                privateKeyHex = privOpt.get();
            } else {
                ECKeyPair pair = Ecies.generateEcKeyPair();
                publicKeyHex = pair.getPublicHex(false);
                privateKeyHex = pair.getPrivateHex();
                LOGGER.info("Grid plugin generated a new ECIES keypair");
            }

            publicKeyBytes = Hex.decode(publicKeyHex);
            privateKeyBytes = Hex.decode(privateKeyHex);
            LOGGER.info("Grid plugin initialized with ECIES keys");
            LOGGER.info("Encode with: https://dzharikhin.github.io/ecies/?pk=" + publicKeyHex);
        } catch (Exception e) {
            LOGGER.log(Level.SEVERE, "Failed to load ECIES keys", e);
            throw new RuntimeException(e);
        }

        bus.addListener(SessionCreatedEvent.listener(data -> {
            SessionId sid = data.getSessionId();
            Capabilities caps = data.getCapabilities();
            Object bidiUrl = caps.getCapability("webSocketUrl");
            if (bidiUrl instanceof String) {
                sessionBidiUrls.put(sid, (String) bidiUrl);
                LOGGER.info("Session " + sid + " has BiDi URL: " + bidiUrl);
            }
        }));

        bus.addListener(SessionClosedEvent.listener(data -> {
            SessionId sid = data.getSessionId();
            closeBiDiClient(sid);
            sessionBidiUrls.remove(sid);
        }));
    }

    private void closeBiDiClient(SessionId sid) {
        BiDiClient client = bidiClients.remove(sid);
        if (client != null) {
            try {
                client.close();
            } catch (Exception e) {
                LOGGER.fine("Failed to close BiDiClient for session " + sid + ": " + e.getMessage());
            }
        }
    }

    @Nullable
    @Override
    public HttpResponse intercept(SessionId id, HttpRequest req, Callable<HttpResponse> next)
        throws Exception {

        String method = req.getMethod().toString();
        String uri = req.getUri();
        LOGGER.info("intercept: " + method + " " + uri + " sessionId=" + id);

        if ("POST".equals(method) && uri.contains("/cookie")) {
            String body = req.contentAsString();
            body = decryptCookieValues(body);
            req.setContent(Contents.bytes(body.getBytes(StandardCharsets.UTF_8)));

            HttpResponse resp = next.call();

            String respBody = resp.contentAsString();
            if (respBody.toLowerCase().contains("invalid cookie domain")) {
                String bidiUrl = sessionBidiUrls.get(id);
                if (bidiUrl == null || bidiUrl.isEmpty()) {
                    throw new RuntimeException("InvalidCookieDomain but no BiDi URL available (session may not have webSocketUrl)");
                }

                JsonObject decryptedCookie = JsonParser.parseString(body).getAsJsonObject().getAsJsonObject("cookie");
                String cookieName = decryptedCookie.has("name") ? decryptedCookie.get("name").getAsString() : "?";
                String cookieDomain = decryptedCookie.has("domain") ? decryptedCookie.get("domain").getAsString() : "?";

                BiDiClient bidi = bidiClients.computeIfAbsent(id, sid -> new BiDiClient(bidiUrl));
                bidi.ensureSession();

                JsonObject bidiCookie = CookieConverter.classicToBiDi(decryptedCookie);
                JsonObject params = new JsonObject();
                params.add("cookie", bidiCookie);

                try {
                    bidi.sendCommand("storage.setCookie", params).join();
                    LOGGER.info("Set cookie " + cookieName + " for domain " + cookieDomain + " via BiDi (InvalidCookieDomain fallback)");
                    HttpResponse newResp = new HttpResponse();
                    newResp.setStatus(200);
                    newResp.setContent(Contents.bytes("{\"value\": null}".getBytes(StandardCharsets.UTF_8)));
                    return newResp;
                } catch (Exception e) {
                    LOGGER.warning("BiDi setCookie failed for " + cookieName + ": " + e.getMessage());
                    throw new RuntimeException("BiDi setCookie failed", e);
                }
            }

            return resp;
        }

        if ("POST".equals(method) && uri.contains("/value")) {
            String body = req.contentAsString();
            body = decryptValue(body);
            req.setContent(Contents.bytes(body.getBytes(StandardCharsets.UTF_8)));
        }

        HttpResponse resp = next.call();

        if ("GET".equals(method) && uri.contains("/cookie")) {
            String bidiUrl = sessionBidiUrls.get(id);
            if (bidiUrl == null || bidiUrl.isEmpty()) {
                throw new RuntimeException("GET /cookie but no BiDi URL available (session may not have webSocketUrl)");
            }

            BiDiClient bidi = bidiClients.computeIfAbsent(id, sid -> new BiDiClient(bidiUrl));
            bidi.ensureSession();

            try {
                JsonObject result = bidi.sendCommand("storage.getCookies", new JsonObject()).join();
                JsonArray cookiesArray = result.getAsJsonArray("cookies");
                JsonArray classicCookies = new JsonArray();
                for (JsonElement el : cookiesArray) {
                    if (el.isJsonObject()) {
                        classicCookies.add(CookieConverter.biDiToClassic(el.getAsJsonObject()));
                    }
                }

                JsonObject wrapped = new JsonObject();
                wrapped.add("value", classicCookies);
                String encrypted = encryptCookieValues(wrapped.toString());
                resp.setContent(Contents.bytes(encrypted.getBytes(StandardCharsets.UTF_8)));
            } catch (Exception e) {
                LOGGER.warning("BiDi getCookies failed: " + e.getMessage());
                throw new RuntimeException("BiDi getCookies failed", e);
            }
        }

        return resp;
    }

    @Override
    public void close() throws IOException {
        for (SessionId sid : bidiClients.keySet()) {
            closeBiDiClient(sid);
        }
        bidiClients.clear();
        sessionBidiUrls.clear();
    }

    private String decryptCookieValues(String json) {
        try {
            JsonObject root = JsonParser.parseString(json).getAsJsonObject();
            if (root.has("cookie") && root.get("cookie").isJsonObject()) {
                decryptCookieObject(root.getAsJsonObject("cookie"));
            }
            if (root.has("cookies") && root.get("cookies").isJsonArray()) {
                for (JsonElement el : root.getAsJsonArray("cookies")) {
                    if (el.isJsonObject()) decryptCookieObject(el.getAsJsonObject());
                }
            }
            return root.toString();
        } catch (Exception e) {
            LOGGER.log(Level.FINE, "decryptCookieValues failed, passing through: {0}", e.getMessage());
            return json;
        }
    }

    private void decryptCookieObject(JsonObject obj) {
        if (!obj.has("value") || !obj.get("value").isJsonPrimitive()) return;
        String encrypted = obj.get("value").getAsString();
        try {
            byte[] encryptedBytes = Base64.getDecoder().decode(encrypted);
            byte[] decrypted = Ecies.decrypt(privateKeyBytes, encryptedBytes);
            obj.addProperty("value", new String(decrypted, StandardCharsets.UTF_8));
        } catch (Exception e) {
            LOGGER.log(Level.FINE, "Failed to decrypt cookie value, passing through: {0}", e.getMessage());
        }
    }

    private String encryptCookieValues(String json) {
        try {
            JsonElement root = JsonParser.parseString(json);
            encryptCookieValuesIn(root);
            return root.toString();
        } catch (Exception e) {
            LOGGER.log(Level.FINE, "encryptCookieValues failed, passing through: {0}", e.getMessage());
            return json;
        }
    }

    private void encryptCookieValuesIn(JsonElement el) {
        if (el.isJsonObject()) {
            JsonObject obj = el.getAsJsonObject();
            if (obj.has("name") && obj.has("value") && obj.get("value").isJsonPrimitive()) {
                String plain = obj.get("value").getAsString();
                try {
                    byte[] encrypted = Ecies.encrypt(publicKeyBytes, plain.getBytes(StandardCharsets.UTF_8));
                    obj.addProperty("value", Base64.getEncoder().encodeToString(encrypted));
                } catch (Exception e) {
                    LOGGER.log(Level.FINE, "Failed to encrypt cookie value: {0}", e.getMessage());
                }
            }
            for (var entry : obj.entrySet()) encryptCookieValuesIn(entry.getValue());
        } else if (el.isJsonArray()) {
            for (JsonElement e : el.getAsJsonArray()) encryptCookieValuesIn(e);
        }
    }

    private String decryptValue(String json) {
        try {
            JsonObject root = JsonParser.parseString(json).getAsJsonObject();
            if (root.has("value") && root.get("value").isJsonArray()) {
                JsonArray arr = root.getAsJsonArray("value");
                StringBuilder sb = new StringBuilder();
                for (JsonElement el : arr) {
                    if (!el.isJsonPrimitive()) return json;   // not a typed string; pass through unchanged
                    sb.append(el.getAsString());
                }
                String joined = sb.toString();
                try {
                    byte[] encryptedBytes = Hex.decode(joined);
                    byte[] decrypted = Ecies.decrypt(privateKeyBytes, encryptedBytes);
                    String plaintext = new String(decrypted, StandardCharsets.UTF_8);
                    JsonArray newArr = new JsonArray();
                    for (char c : plaintext.toCharArray()) newArr.add(String.valueOf(c));
                    root.add("value", newArr);
                    root.addProperty("text", plaintext);
                } catch (Exception e) {
                    LOGGER.log(Level.FINE, "Failed to decrypt joined value, passing through: {0}", e.getMessage());
                }
            }
            return root.toString();
        } catch (Exception e) {
            LOGGER.log(Level.FINE, "decryptValue failed, passing through: {0}", e.getMessage());
            return json;
        }
    }
}
