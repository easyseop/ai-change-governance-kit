import java.util.concurrent.ExecutorService;

class Vault { void transfer() { int value = 2; } }
class Flow {
    ExecutorService pool;
    void sink() { helper(); }
    void helper() { pool.submit(() -> new Vault().transfer()); }
}
