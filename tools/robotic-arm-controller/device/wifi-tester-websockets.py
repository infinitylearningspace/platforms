# Ultra-lightweight MicroPython Robotic Arm Controller
# Extreme memory optimization for ESP8266/ESP32

import asyncio
import json
import gc
from machine import Pin, PWM
import network

# Platform detection
try:
    from machine import unique_id
    IS_ESP32 = len(unique_id()) == 6
except:
    IS_ESP32 = False

# Configurations
WIFI_SSID = "Infinity-3rd-2.4"
WIFI_PASSWORD = "9880736444"

if IS_ESP32:
    SERVO_PINS = [2, 4, 5, 18]        
else:   
    SERVO_PINS = [14, 12, 13, 15]

LED_PIN = None
# Minimal servo class
class Servo:
    __slots__ = ['p', 'a']
    
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
    __slots__ = ['s', 'connected']
    
    def __init__(self, socket):
        self.s = socket
        self.connected = True

    def read_frame(self):
        try:
            # Read header (2 bytes minimum)
            data = self.s.recv(2)
            if len(data) < 2:
                return None, None
            
            b1, b2 = data[0], data[1]
            opcode = b1 & 0xf
            masked = (b2 >> 7) & 1
            payload_len = b2 & 0x7f
            
            # Handle extended length
            if payload_len == 126:
                data = self.s.recv(2)
                if len(data) < 2:
                    return None, None
                payload_len = (data[0] << 8) | data[1]
            elif payload_len == 127:
                # Skip 64-bit length - too big for microcontroller
                return None, None
            
            # Read mask if present
            if masked:
                mask = self.s.recv(4)
                if len(mask) < 4:
                    return None, None
            
            # Read payload
            payload = b''
            if payload_len > 0:
                if payload_len > 1024:  # Limit payload size
                    return None, None
                    
                payload = self.s.recv(payload_len)
                if len(payload) < payload_len:
                    return None, None
                
                # Unmask payload if needed
                if masked:
                    payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
            
            return opcode, payload
            
        except OSError as e:
            # Handle EAGAIN (11) as no data available
            if e.args[0] == 11:  # EAGAIN
                return None, None
            # Other errors mean connection issue
            self.connected = False
            return None, None
        except Exception:
            self.connected = False
            return None, None

    def send_text(self, text):
        if not self.connected:
            return False
        try:
            # Build frame
            data = text.encode('utf-8')
            frame = bytearray()
            frame.append(0x81)  # FIN + text frame
            
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

# Main controller - minimal version
class ArmCtrl:
    def __init__(self):
        self.ssid = WIFI_SSID
        self.pwd = WIFI_PASSWORD
        
        # Hardware
        pins = SERVO_PINS
        self.servos = [Servo(pin) for pin in pins]
        self.q = []
        self.stop = False
        self.clients = []
        
        # LED
        try:
            self.led = Pin(LED_PIN)
        except:
            self.led = None
        
        # Force cleanup
        gc.collect()
    
    def wifi_connect(self):
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        
        if sta.isconnected():
            print(f"IP: {sta.ifconfig()[0]}")
            return True
        
        print("Connecting...")
        sta.connect(self.ssid, self.pwd)
        
        for _ in range(20):
            if sta.isconnected():
                print(f"IP: {sta.ifconfig()[0]}")
                return True
            import time
            time.sleep(1)
        
        return False

    def ws_handshake(self, client_socket):
        try:
            # Keep socket blocking for handshake
            client_socket.setblocking(True)
            
            # Read HTTP request in one go
            request = client_socket.recv(1024)
            if not request:
                return False
            
            # Parse request
            request_str = request.decode('utf-8')
            lines = request_str.split('\n')
            
            # Find WebSocket key
            key = None
            for line in lines:
                if 'sec-websocket-key' in line.lower():
                    key = line.split(':', 1)[1].strip()
                    break
            
            if not key:
                print("No WebSocket key found")
                return False
            
            # Generate response key
            import hashlib
            import binascii
            magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            combined = key + magic
            sha1 = hashlib.sha1(combined.encode()).digest()
            accept_key = binascii.b2a_base64(sha1).decode().strip()
            
            # Send handshake response
            response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept_key}\r\n\r\n"
            )
            
            client_socket.send(response.encode())
            
            # Switch to non-blocking
            client_socket.setblocking(False)
            
            return True
            
        except Exception as e:
            print(f"Handshake error: {e}")
            return False

    def handle_message(self, client, payload):
        try:
            data = json.loads(payload.decode())
            cmd = data.get('t', '')
            
            if cmd == 'move':
                j = data.get('j', 0)
                a = data.get('a', 90)
                if 0 <= j < len(self.servos) and 0 <= a <= 180:
                    self.q.append((j, a))
                    resp = '{"t":"ack","d":"ok"}'
                else:
                    resp = '{"t":"error","m":"invalid"}'
                client.send_text(resp)
                
            elif cmd == 'stop':
                self.stop = True
                self.q = []
                client.send_text('{"t":"ack","d":"stopped"}')
                
            elif cmd == 'home':
                for i in range(len(self.servos)):
                    self.q.append((i, 90))
                client.send_text('{"t":"ack","d":"homing"}')
                
            elif cmd == 'status':
                angles = [s.a for s in self.servos]
                status = {
                    't': 'status',
                    'd': {
                        'angles': angles,
                        'mem': gc.mem_free(),
                        'stop': self.stop
                    }
                }
                client.send_text(json.dumps(status))
                
        except Exception as e:
            print(f"Msg error: {e}")

    def servo_loop(self):
        """Non-async servo control"""
        if self.stop:
            import time
            time.sleep(2)
            self.stop = False
            return
            
        if self.q:
            j, a = self.q.pop(0)
            if j < len(self.servos):
                self.servos[j].move(a)
                print(f"J{j}={a}")
                
                if self.led:
                    self.led.on()
                    import time
                    time.sleep(0.01)
                    self.led.off()

    def run(self):
        print("ARM CTRL Lite v1.0")
        print(f"Mem: {gc.mem_free()}")
        
        if not self.wifi_connect():
            print("WiFi failed")
            return
        
        # Create server socket
        server_socket = None
        try:
            import socket
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind(('0.0.0.0', 81))
            server_socket.listen(2)  # Max 2 clients
            server_socket.setblocking(False)
            print("Server ready on port 81")
            
        except Exception as e:
            print(f"Server error: {e}")
            return
        
        print("READY!")
        
        # Main loop
        while True:
            try:
                # Handle servo movements
                self.servo_loop()
                
                # Check for new connections (non-blocking)
                try:
                    client_socket, addr = server_socket.accept()
                    print(f"New connection from: {addr[0]}")
                    
                    # Handle handshake in blocking mode, then switch to non-blocking
                    if self.ws_handshake(client_socket):
                        client = WSClient(client_socket)
                        self.clients.append(client)
                        print("WebSocket handshake successful")
                        
                        # Send initial status
                        try:
                            status = f'{{"t":"status","d":{{"mem":{gc.mem_free()},"ready":true}}}}'
                            client.send_text(status)
                        except:
                            pass
                    else:
                        print("WebSocket handshake failed")
                        try:
                            client_socket.close()
                        except:
                            pass
                        
                except OSError as e:
                    if e.args[0] != 11:  # Not EAGAIN (no connections waiting)
                        print(f"Accept error: {e}")
                except Exception as e:
                    print(f"Connection error: {e}")
                
                # Handle existing clients
                disconnected = []
                for client in self.clients:
                    if not client.connected:
                        disconnected.append(client)
                        continue
                        
                    try:
                        opcode, payload = client.read_frame()
                        if opcode is None:
                            continue
                        elif opcode == 8:  # Close
                            disconnected.append(client)
                        elif opcode == 1 and payload:  # Text
                            self.handle_message(client, payload)
                    except OSError:
                        pass  # No data available
                    except:
                        disconnected.append(client)
                
                # Clean up disconnected clients
                for client in disconnected:
                    client.close()
                    if client in self.clients:
                        self.clients.remove(client)
                
                # Memory management
                if gc.mem_free() < 5000:
                    gc.collect()
                    print(f"GC: {gc.mem_free()}")
                
                # Small delay
                import time
                time.sleep(0.01)
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"Loop error: {e}")
                import time
                time.sleep(0.1)
        
        # Cleanup
        if server_socket:
            server_socket.close()
        for client in self.clients:
            client.close()

# Entry point
def main():
    ctrl = ArmCtrl()
    ctrl.run()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fatal: {e}")
        import time
        time.sleep(2)
        try:
            import machine
            machine.reset()
        except:
            pass