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
import java.util.logging.Level;
import java.util.logging.Logger;
import org.bouncycastle.util.encoders.Hex;
import org.jspecify.annotations.Nullable;
import org.openqa.selenium.events.EventBus;
import org.openqa.selenium.grid.config.Config;
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
            LOGGER.log(Level.SEVERE, "Failed to load ECIES keys: {0}", e.getMessage());
            throw new RuntimeException(e);
        }
    }

    @Nullable
    @Override
    public HttpResponse intercept(SessionId id, HttpRequest req, Callable<HttpResponse> next)
        throws Exception {

        String method = req.getMethod().toString();
        String uri = req.getUri();

        if ("POST".equals(method) && uri.contains("/cookie")) {
            String body = req.contentAsString();
            body = decryptCookieValues(body);
            req.setContent(Contents.bytes(body.getBytes(StandardCharsets.UTF_8)));
        }

        if ("POST".equals(method) && uri.contains("/value")) {
            String body = req.contentAsString();
            body = decryptValue(body);
            req.setContent(Contents.bytes(body.getBytes(StandardCharsets.UTF_8)));
        }

        HttpResponse resp = next.call();

        if ("GET".equals(method) && uri.contains("/cookie")) {
            String body = resp.contentAsString();
            body = encryptCookieValues(body);
            resp.setContent(Contents.bytes(body.getBytes(StandardCharsets.UTF_8)));
        }

        return resp;
    }

    @Override
    public void close() throws IOException {}

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
                for (int i = 0; i < arr.size(); i++) {
                    JsonElement el = arr.get(i);
                    if (!el.isJsonPrimitive()) continue;
                    String encrypted = el.getAsString();
                    try {
                        byte[] encryptedBytes = Hex.decode(encrypted);
                        byte[] decrypted = Ecies.decrypt(privateKeyBytes, encryptedBytes);
                        arr.set(i, new JsonPrimitive(new String(decrypted, StandardCharsets.UTF_8)));
                    } catch (Exception e) {
                        LOGGER.log(Level.FINE, "Failed to decrypt value, passing through: {0}", e.getMessage());
                    }
                }
            }
            return root.toString();
        } catch (Exception e) {
            LOGGER.log(Level.FINE, "decryptValue failed, passing through: {0}", e.getMessage());
            return json;
        }
    }
}
