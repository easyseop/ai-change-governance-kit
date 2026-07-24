def run_command(cmd):
    import subprocess

    return subprocess.run(cmd, check=True)


def load_customer(cursor, account_id):
    query = "select * from customer_pii where account_id = ?"
    return cursor.execute(query, [account_id])


def harmless(value):
    return value.strip()
