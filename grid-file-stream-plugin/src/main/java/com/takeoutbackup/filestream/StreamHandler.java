package com.takeoutbackup.filestream;

import javax.crypto.Cipher;
import javax.crypto.Mac;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Base64;
import java.util.Iterator;
import java.util.concurrent.ConcurrentHashMap;
import java.util.logging.Logger;

public class StreamHandler {
    private static final Logger LOGGER = Logger.getLogger(StreamHandler.class.getName());
    private final ConcurrentHashMap<String, Boolean> sessionDirs;
    private final String downloadsDir;

    public StreamHandler(ConcurrentHashMap<String, Boolean> sessionDirs, String downloadsDir) {
        this.sessionDirs = sessionDirs;
        this.downloadsDir = downloadsDir;
    }

    public void handle(Socket socket) throws IOException {
        socket.setTcpNoDelay(true);
        InputStream in = new BufferedInputStream(socket.getInputStream());
        OutputStream out = socket.getOutputStream();

        try {
            String requestLine = readLine(in);
            if (requestLine == null || requestLine.isEmpty()) {
                sendStatus(out, 400, "Bad Request");
                return;
            }
            String[] requestParts = requestLine.split(" ");
            if (requestParts.length < 3 || !"GET".equals(requestParts[0])) {
                sendStatus(out, 405, "Method Not Allowed");
                return;
            }
            String path = requestParts[1];

            String clientNonceBase64 = null;
            String line;
            while ((line = readLine(in)) != null && !line.isEmpty()) {
                int idx = line.indexOf(':');
                if (idx > 0) {
                    String name = line.substring(0, idx).trim();
                    String value = line.substring(idx + 1).trim();
                    if (name.equalsIgnoreCase("X-Stream-Nonce")) {
                        clientNonceBase64 = value;
                    }
                }
            }

            serve(path, clientNonceBase64, out);
        } catch (Exception e) {
            LOGGER.warning("Error handling file-stream request: " + e.getMessage());
            try {
                sendStatus(out, 500, "Internal Server Error");
            } catch (IOException ignored) {
            }
        }
    }

    private void serve(String path, String clientNonceBase64, OutputStream out) throws Exception {
        if (!path.startsWith("/download/")) {
            sendStatus(out, 400, "Bad Request");
            return;
        }
        String[] parts = path.substring("/download/".length()).split("/", 2);
        if (parts.length != 2) {
            sendStatus(out, 400, "Bad Request");
            return;
        }
        String sessionId = parts[0];
        String filename = parts[1];

        if (!sessionDirs.containsKey(sessionId)) {
            sendStatus(out, 403, "Forbidden");
            return;
        }
        if (filename.contains("..") || filename.contains("/") || filename.contains("\\")) {
            sendStatus(out, 400, "Bad Request");
            return;
        }
        if (clientNonceBase64 == null || clientNonceBase64.isEmpty()) {
            sendStatus(out, 400, "Bad Request");
            return;
        }

        byte[] clientNonce;
        try {
            clientNonce = Base64.getDecoder().decode(clientNonceBase64);
        } catch (IllegalArgumentException e) {
            sendStatus(out, 400, "Bad Request");
            return;
        }
        if (clientNonce.length != 16) {
            sendStatus(out, 400, "Bad Request");
            return;
        }

        String pskHex = System.getProperty("filestream.key", "");
        if (pskHex.length() != 64) {
            sendStatus(out, 500, "Internal Server Error");
            return;
        }

        byte[] psk = hexToBytes(pskHex);
        byte[] K = hkdf(psk, clientNonce);

        Path filePath = findFile(downloadsDir, filename);
        if (filePath == null || !Files.exists(filePath) || Files.size(filePath) == 0) {
            sendStatus(out, 404, "Not Found");
            return;
        }

        long plainLength = filePath.toFile().length();
        long n = (plainLength + 65536 - 1) / 65536;
        long contentLength = 4L * n + plainLength + 16L * n;

        StringBuilder headers = new StringBuilder();
        headers.append("HTTP/1.1 200 OK\r\n");
        headers.append("X-Stream-Nonce: ").append(clientNonceBase64).append("\r\n");
        headers.append("X-Plain-Length: ").append(plainLength).append("\r\n");
        headers.append("Content-Type: application/octet-stream\r\n");
        headers.append("Content-Length: ").append(contentLength).append("\r\n");
        headers.append("Connection: close\r\n");
        headers.append("\r\n");
        out.write(headers.toString().getBytes(StandardCharsets.ISO_8859_1));
        out.flush();

        try (InputStream is = Files.newInputStream(filePath)) {
            long counter = 0;
            byte[] buf = new byte[65536];
            int bytesRead;
            while ((bytesRead = is.read(buf)) > 0) {
                byte[] nonce12 = longToBytes12(counter);
                GCMParameterSpec gcmSpec = new GCMParameterSpec(128, nonce12);

                Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
                cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(K, "AES"), gcmSpec);
                byte[] ciphertext = cipher.doFinal(buf, 0, bytesRead);
                byte[] frame = new byte[bytesRead + 16];
                System.arraycopy(ciphertext, 0, frame, 0, bytesRead);
                System.arraycopy(ciphertext, bytesRead, frame, bytesRead, 16);

                out.write(intToBytesBigEndian(frame.length));
                out.write(frame);
                counter++;
            }
        }
        out.flush();
    }

    private void sendStatus(OutputStream out, int status, String reason) throws IOException {
        byte[] body = (status + " " + reason + "\n").getBytes(StandardCharsets.UTF_8);
        StringBuilder sb = new StringBuilder();
        sb.append("HTTP/1.1 ").append(status).append(" ").append(reason).append("\r\n");
        sb.append("Content-Type: text/plain; charset=utf-8\r\n");
        sb.append("Content-Length: ").append(body.length).append("\r\n");
        sb.append("Connection: close\r\n");
        sb.append("\r\n");
        out.write(sb.toString().getBytes(StandardCharsets.ISO_8859_1));
        out.write(body);
        out.flush();
    }

    private String readLine(InputStream in) throws IOException {
        ByteArrayOutputStream baos = new ByteArrayOutputStream(128);
        int c;
        boolean any = false;
        while ((c = in.read()) != -1) {
            any = true;
            if (c == '\n') {
                break;
            }
            baos.write(c);
        }
        if (!any && baos.size() == 0) {
            return null;
        }
        byte[] arr = baos.toByteArray();
        int len = arr.length;
        if (len > 0 && arr[len - 1] == '\r') {
            len--;
        }
        return new String(arr, 0, len, StandardCharsets.ISO_8859_1);
    }

    private byte[] hkdf(byte[] psk, byte[] salt) throws Exception {
        Mac m1 = Mac.getInstance("HmacSHA256");
        m1.init(new SecretKeySpec(salt, "HmacSHA256"));
        byte[] prk = m1.doFinal(psk);

        Mac m2 = Mac.getInstance("HmacSHA256");
        m2.init(new SecretKeySpec(prk, "HmacSHA256"));
        m2.update("takeout-file-stream-v1".getBytes(StandardCharsets.UTF_8));
        m2.update((byte) 1);
        return m2.doFinal();
    }

    private Path findFile(String root, String filename) {
        Path rootPath = Paths.get(root);

        Path direct = rootPath.resolve(filename);
        if (Files.exists(direct) && !Files.isDirectory(direct)) {
            return direct;
        }

        try {
            Iterator<Path> iterator = Files.walk(rootPath, 5).iterator();
            while (iterator.hasNext()) {
                Path p = iterator.next();
                if (!Files.isDirectory(p) && p.getFileName().toString().equals(filename)) {
                    return p;
                }
            }
        } catch (IOException e) {
            LOGGER.warning("Error searching for file " + filename + ": " + e.getMessage());
        }

        return null;
    }

    private byte[] longToBytes12(long value) {
        byte[] bytes = new byte[12];
        for (int i = 11; i >= 0; i--) {
            bytes[i] = (byte) (value & 0xFF);
            value >>= 8;
        }
        return bytes;
    }

    private byte[] intToBytesBigEndian(int value) {
        return new byte[] {
            (byte) (value >> 24),
            (byte) (value >> 16),
            (byte) (value >> 8),
            (byte) value
        };
    }

    private byte[] hexToBytes(String hex) {
        if (hex.length() % 2 != 0) {
            throw new IllegalArgumentException("Hex string must have even length");
        }
        byte[] bytes = new byte[hex.length() / 2];
        for (int i = 0; i < hex.length(); i += 2) {
            bytes[i / 2] = (byte) ((Character.digit(hex.charAt(i), 16) << 4)
                    + Character.digit(hex.charAt(i + 1), 16));
        }
        return bytes;
    }
}
