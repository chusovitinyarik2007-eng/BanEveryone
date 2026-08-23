import sys
from scapy.all import *
from scapy.layers.inet import IP, TCP, ICMP

# Целевой хост
target = "main.service-ya.fun"

print(f"Запуск трассировки до {target}...")

for ttl in range(1, 20):
    # Создаем пакет: dport вместо dnf
    pkt = IP(dst=target, ttl=ttl) / TCP(sport=RandShort(), dport=443, flags="S")

    # Отправляем пакет. На Mac лучше не указывать конкретный iface сразу,
    # но если ответов не будет совсем — добавьте iface="en0" внутрь sr1()
    reply = sr1(pkt, timeout=2, verbose=0, promisc=False)

    if reply:
        # Проверяем, есть ли TCP слой в ответе (некоторые узлы ответят ICMP)
        if reply.haslayer(TCP):
            flags = reply[TCP].flags
            print(f"Hop {ttl}: Ответил {reply.src} с TCP-флагами {flags}")
        # Если промежуточный роутер ответил стандартным ICMP Time Exceeded
        elif reply.haslayer(ICMP):
            print(f"Hop {ttl}: Роутер {reply.src} (ICMP Time Exceeded)")
        else:
            print(f"Hop {ttl}: Ответил {reply.src} (Другой протокол)")

        # Если дошли до целевого сервера (проверяем по IP)
        if reply.src == target or reply.src == socket.gethostbyname(target):
            print("Успешно достигли целевого сервера!")
            break
    else:
        print(f"Hop {ttl}: * * * (Таймаут)")
