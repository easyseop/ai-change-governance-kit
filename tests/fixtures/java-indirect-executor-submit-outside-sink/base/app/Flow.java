import java.util.concurrent.ExecutorService;

class Vault { void transfer() { int value = 1; } }
class Flow {
    ExecutorService pool;
    void wire() { pool.submit(() -> new Vault().transfer()); }
    void sink() { pool.shutdown(); }
}
