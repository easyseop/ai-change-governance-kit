import java.io.ObjectInputStream;
import javax.crypto.Cipher;
import org.springframework.web.client.RestTemplate;

class CapabilityExamples {
    void run(ObjectInputStream ois, String cmd, String sql) throws Exception {
        Runtime.getRuntime().exec(cmd);
        new ProcessBuilder(cmd).start();
        ois.readObject();
        Class.forName("com.acme.Plugin").getMethod("run");
        java.sql.Statement statement = null;
        statement.execute(sql);
        new javax.naming.InitialContext().lookup("ldap://example");
        new RestTemplate().getForObject("https://example.invalid", String.class);
        Cipher.getInstance("AES/GCM/NoPadding");
        java.security.MessageDigest.getInstance("SHA-256");
        dynamic.invoke(this);
    }
}
