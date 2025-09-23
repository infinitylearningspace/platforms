# Ultra-minimal MicroPython Robotic Arm Controller
# Maximum memory optimization for ESP8266
# Conditional PCA9685 support

import gc
from machine import Pin, PWM
import network

# Force early garbage collection
gc.collect()

# Platform detection (minimal)
try:
    from machine import unique_id
    IS_ESP32 = len(unique_id()) == 6
except:
    IS_ESP32 = False

# Configurations
WIFI_SSID = "SSID"
WIFI_PASSWORD = "PASSWD"

# Servo controller configuration
USE_PCA9685 = False  # Set to True to enable PCA9685 support

# Conditional imports - ONLY import what we need
if USE_PCA9685:
    from machine import I2C
    # PCA9685 configuration
    PCA9685_ADDRESS = 0x40
    PCA9685_FREQUENCY = 50
    if IS_ESP32:
        I2C_SCL, I2C_SDA = 22, 21
    else:
        I2C_SCL, I2C_SDA = 5, 4
    PCA9685_CHANNELS = [0, 1, 2, 3]
else:
    # Direct PWM configuration
    if IS_ESP32:
        SERVO_PINS = [2, 4, 5, 18]
    else:   
        SERVO_PINS = [14, 12, 13, 15]

# Force cleanup after imports
gc.collect()

# Conditional PCA9685 class (only if needed)
if USE_PCA9685:
    class PCA9685:
        def __init__(self, i2c, addr=0x40, freq=50):
            self.i2c = i2c
            self.addr = addr
            try:
                self.i2c.writeto_mem(addr, 0x00, b'\x00')  # Reset
                prescale = int(25000000.0 / (4096 * freq) - 1)
                old_mode = self.i2c.readfrom_mem(addr, 0x00, 1)[0]
                self.i2c.writeto_mem(addr, 0x00, bytes([(old_mode & 0x7F) | 0x10]))
                self.i2c.writeto_mem(addr, 0xFE, bytes([prescale]))
                self.i2c.writeto_mem(addr, 0x00, bytes([old_mode]))
                import time
                time.sleep_ms(5)
                self.i2c.writeto_mem(addr, 0x00, bytes([old_mode | 0xa1]))
                print(f"PCA ok")
            except Exception as e:
                print(f"PCA err: {e}")
        
        def set_pwm(self, ch, on, off):
            try:
                reg = 0x06 + 4 * ch
                data = bytes([on & 0xFF, on >> 8, off & 0xFF, off >> 8])
                self.i2c.writeto_mem(self.addr, reg, data)
            except:
                pass

# Minimal servo classes (different for each mode)
if USE_PCA9685:
    class Servo:
        def __init__(self, ch, pca):
            self.pca = pca
            self.ch = ch
            self.a = 90
            self.move(90)
        
        def move(self, angle):
            self.a = max(0, min(180, angle))
            pulse_us = 500 + (2500 - 500) * self.a / 180
            pulse_counts = int(pulse_us / (20000 / 4096))
            self.pca.set_pwm(self.ch, 0, pulse_counts)
else:
    class Servo:
        def __init__(self, pin):
            self.p = PWM(Pin(pin))
            self.p.freq(50)
            self.a = 90
            self.move(90)
        
        def move(self, angle):
            self.a = max(0, min(180, angle))
            d = int((500 + self.a * 11.11) * 1024 / 20000)
            self.p.duty(d)

# Ultra-minimal WebSocket client
class WSClient:
    def __init__(self, s):
        self.s = s
        self.connected = True

    def read_frame(self):
        try:
            data = self.s.recv(2)
            if len(data) < 2:
                return None, None
            
            b1, b2 = data[0], data[1]
            opcode = b1 & 0xf
            masked = (b2 >> 7) & 1
            payload_len = b2 & 0x7f
            
            if payload_len == 126:
                data = self.s.recv(2)
                if len(data) < 2:
                    return None, None
                payload_len = (data[0] << 8) | data[1]
            elif payload_len == 127:
                return None, None
            
            if masked:
                mask = self.s.recv(4)
                if len(mask) < 4:
                    return None, None
            
            payload = b''
            if payload_len > 0:
                if payload_len > 512:  # Reduced limit
                    return None, None
                payload = self.s.recv(payload_len)
                if len(payload) < payload_len:
                    return None, None
                if masked:
                    payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
            
            return opcode, payload
            
        except OSError as e:
            if e.args[0] == 11:  # EAGAIN
                return None, None
            self.connected = False
            return None, None
        except:
            self.connected = False
            return None, None

    def send_text(self, text):
        if not self.connected:
            return False
        try:
            data = text.encode('utf-8')
            frame = bytearray([0x81])  # FIN + text frame
            
            if len(data) < 126:
                frame.append(len(data))
            else:
                frame.append(126)
                frame.extend(len(data).to_bytes(2, 'big'))
            
            frame.extend(data)
            self.s.send(frame)
            return True
        except:
            self.connected = False
            return False

    def close(self):
        if self.connected:
            try:
                self.s.send(b'\x88\x00')  # Close frame
            except:
                pass
            self.connected = False
        try:
            self.s.close()
        except:
            pass

# Minimal controller
class ArmCtrl:
    def __init__(self):
        self.ssid = WIFI_SSID
        self.pwd = WIFI_PASSWORD
        
        # Initialize hardware
        if USE_PCA9685:
            self.pca = self.init_pca()
        else:
            self.pca = None
        
        self.init_servos()
        
        self.q = []
        self.stop = False
        self.clients = []
        
        gc.collect()
        print(f"Mem: {gc.mem_free()}")
    
    if USE_PCA9685:
        def init_pca(self):
            try:
                i2c = I2C(0, scl=Pin(I2C_SCL), sda=Pin(I2C_SDA), freq=100000)
                devices = i2c.scan()
                if PCA9685_ADDRESS not in devices:
                    print("PCA not found")
                    return None
                return PCA9685(i2c, PCA9685_ADDRESS, PCA9685_FREQUENCY)
            except Exception as e:
                print(f"I2C err: {e}")
                return None
    
    def init_servos(self):
        self.servos = []
        if USE_PCA9685 and self.pca:
            for ch in PCA9685_CHANNELS:
                self.servos.append(Servo(ch, self.pca))
        else:
            for pin in SERVO_PINS:
                self.servos.append(Servo(pin))
    
    def wifi_connect(self):
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        
        if sta.isconnected():
            print(f"IP: {sta.ifconfig()[0]}")
            return True
        
        print("Connecting...")
        sta.connect(self.ssid, self.pwd)
        
        for _ in range(15):  # Reduced timeout
            if sta.isconnected():
                print(f"IP: {sta.ifconfig()[0]}")
                return True
            import time
            time.sleep(1)
        
        return False

    def ws_handshake(self, sock):
        try:
            sock.setblocking(True)
            request = sock.recv(1024)
            if not request:
                return False
            
            # Find WebSocket key
            key = None
            for line in request.decode().split('\n'):
                if 'sec-websocket-key' in line.lower():
                    key = line.split(':', 1)[1].strip()
                    break
            
            if not key:
                return False
            
            # Generate response key
            import hashlib
            import binascii
            magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            sha1 = hashlib.sha1((key + magic).encode()).digest()
            accept_key = binascii.b2a_base64(sha1).decode().strip()
            
            # Send response
            response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept_key}\r\n\r\n"
            )
            
            sock.send(response.encode())
            sock.setblocking(False)
            return True
            
        except Exception as e:
            print(f"HS err: {e}")
            return False

    def handle_msg(self, client, payload):
        try:
            import json
            data = json.loads(payload.decode())
            cmd = data.get('t', '')
            
            if cmd == 'move':
                j = data.get('j', 0)
                a = data.get('a', 90)
                if 0 <= j < len(self.servos) and 0 <= a <= 180:
                    self.q.append((j, a))
                    client.send_text('{"t":"ack"}')
                else:
                    client.send_text('{"t":"err"}')
                    
            elif cmd == 'stop':
                self.stop = True
                self.q = []
                client.send_text('{"t":"ack"}')
                
            elif cmd == 'home':
                for i in range(len(self.servos)):
                    self.q.append((i, 90))
                client.send_text('{"t":"ack"}')
                
            elif cmd == 'status':
                angles = [s.a for s in self.servos]
                status = f'{{"t":"status","d":{{"angles":{angles},"mem":{gc.mem_free()}}}}}'
                client.send_text(status)
                
        except Exception as e:
            print(f"Msg err: {e}")

    def servo_loop(self):
        if self.stop:
            import time
            time.sleep(1)  # Reduced delay
            self.stop = False
            return
            
        if self.q:
            j, a = self.q.pop(0)
            if j < len(self.servos):
                self.servos[j].move(a)
                print(f"S{j}={a}")

    def run(self):
        print("ARM CTRL Mini")
        
        if not self.wifi_connect():
            print("WiFi failed")
            return
        
        try:
            import socket
            server = socket.socket()
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(('0.0.0.0', 81))
            server.listen(1)  # Only 1 client
            server.setblocking(False)
            print("Server ready")
            
        except Exception as e:
            print(f"Server err: {e}")
            return
        
        print("READY!")
        
        while True:
            try:
                self.servo_loop()
                
                # New connections
                try:
                    sock, addr = server.accept()
                    print(f"Client: {addr[0]}")
                    
                    if self.ws_handshake(sock):
                        client = WSClient(sock)
                        # Remove old client if exists
                        if self.clients:
                            self.clients[0].close()
                            self.clients = []
                        self.clients.append(client)
                        print("WS ok")
                        
                        # Send ready status
                        client.send_text(f'{{"t":"status","d":{{"ready":true,"mem":{gc.mem_free()}}}}}')
                    else:
                        sock.close()
                        
                except OSError as e:
                    if e.args[0] != 11:  # Not EAGAIN
                        print(f"Accept err: {e}")
                
                # Handle existing client
                disconnected = []
                for client in self.clients:
                    if not client.connected:
                        disconnected.append(client)
                        continue
                        
                    try:
                        opcode, payload = client.read_frame()
                        if opcode == 8:  # Close
                            disconnected.append(client)
                        elif opcode == 1 and payload:  # Text
                            self.handle_msg(client, payload)
                    except:
                        disconnected.append(client)
                
                # Cleanup
                for client in disconnected:
                    client.close()
                    if client in self.clients:
                        self.clients.remove(client)
                
                # Aggressive memory management
                if gc.mem_free() < 3000:
                    gc.collect()
                    if gc.mem_free() < 2000:
                        print(f"Low mem: {gc.mem_free()}")
                
                import time
                time.sleep(0.02)  # Slightly longer delay
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"Loop err: {e}")
                gc.collect()
                import time
                time.sleep(0.1)
        
        # Cleanup
        server.close()
        for client in self.clients:
            client.close()

# Entry point
def main():
    gc.collect()
    print(f"Start mem: {gc.mem_free()}")
    
    ctrl = ArmCtrl()
    ctrl.run()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fatal: {e}")
        gc.collect()
        import time
        time.sleep(2)
        try:
            import machine
            machine.reset()
        except:
            pass