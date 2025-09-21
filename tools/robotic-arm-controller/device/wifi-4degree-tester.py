# Ultra-compact MicroPython Robotic Arm Controller
# Extremely memory optimized - reads HTTP requests in small chunks

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

# Minimal servo class
class Servo:
    def __init__(self, pin):
        self.p = PWM(Pin(pin))
        self.p.freq(50)
        self.a = 90
        self.move(90)
    
    def move(self, angle):
        self.a = max(0, min(180, angle))
        d = int((1000 + (self.a / 180) * 1000) * 1024 / 20000)
        self.p.duty(d)

# Main controller
class ArmCtrl:
    def __init__(self):
        # WiFi Configuration - CHANGE THESE VALUES
        self.WIFI_SSID = "Your ssid"
        self.WIFI_PASSWORD = "Your password"
        
        # Hardware config
        pins = [2, 4, 5, 18] if IS_ESP32 else [14, 12, 13, 15]
        self.dof = 4
        self.servos = [Servo(pins[i]) for i in range(self.dof)]
        
        self.q = []
        self.stop = False
        self.is_playing = False
        self.playback_queue = []
        
        # Status LED
        try:
            self.led = Pin(2, Pin.OUT)
        except:
            self.led = None
            
        gc.collect()
    
    async def wifi_setup(self):
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        
        if sta.isconnected():
            print("Connected: " + sta.ifconfig()[0])
            return True
        
        print("Connecting...")
        sta.connect(self.WIFI_SSID, self.WIFI_PASSWORD)
        
        for _ in range(20):
            if sta.isconnected():
                print("IP: " + sta.ifconfig()[0])
                return True
            await asyncio.sleep(1)
        
        print("WiFi failed")
        return False
    
    async def read_http_line(self, reader):
        """Read one line from HTTP request"""
        line = b""
        while True:
            try:
                char = await reader.read(1)
                if not char:
                    return None
                line += char
                if line.endswith(b'\r\n'):
                    return line.decode().strip()
            except:
                return None
    
    async def read_http_request(self, reader):
        """Read HTTP request in tiny chunks to save memory"""
        try:
            # Read request line
            first_line = await self.read_http_line(reader)
            if not first_line:
                return None, None, None
            
            parts = first_line.split(' ')
            if len(parts) < 2:
                return None, None, None
            
            method, path = parts[0], parts[1]
            content_length = 0
            
            # Read headers
            while True:
                line = await self.read_http_line(reader)
                if not line:  # End of headers
                    break
                if line.lower().startswith('content-length:'):
                    content_length = int(line.split(':', 1)[1].strip())
            
            # Read body if present
            body = ""
            if content_length > 0:
                print("Reading body: " + str(content_length) + " bytes")
                for i in range(content_length):
                    char = await reader.read(1)
                    if char:
                        body += char.decode()
                    else:
                        break
                    
                    # Memory check during reading
                    if i % 50 == 0 and gc.mem_free() < 5000:
                        gc.collect()
            
            return method, path, body
            
        except Exception as e:
            print("Read error: " + str(e))
            return None, None, None
    
    async def handle_req(self, r, w):
        try:
            # Force garbage collection before handling request
            gc.collect()
            
            method, path, body = await self.read_http_request(r)
            if not method:
                return
            
            print("REQ: " + method + " " + path)
            
            # Simple CORS headers
            h = "HTTP/1.1 200 OK\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Allow-Methods: GET, POST, OPTIONS\r\nAccess-Control-Allow-Headers: Content-Type\r\n"
            
            if method == "OPTIONS":
                w.write((h + "\r\n").encode())
                
            elif path == "/status":
                # Build response directly to save memory
                angles_str = ",".join([str(s.a) for s in self.servos])
                json_resp = '{"ok":true,"dof":' + str(self.dof) + ',"angles":[' + angles_str + '],"emergency_stop":' + str(self.stop).lower() + ',"is_playing":' + str(self.is_playing).lower() + '}'
                
                # Calculate exact byte length of the JSON response
                json_bytes = json_resp.encode('utf-8')
                content_length = len(json_bytes)
                
                resp = h + "Content-Type: application/json\r\nContent-Length: " + str(content_length) + "\r\n\r\n"
                w.write(resp.encode('utf-8'))
                w.write(json_bytes)
                
            elif path == "/joints" and method == "POST":
                success = self.handle_joint_cmd(body)
                json_resp = '{"ok":true}'
                json_bytes = json_resp.encode('utf-8')
                
                if success:
                    resp = h + "Content-Type: application/json\r\nContent-Length: " + str(len(json_bytes)) + "\r\n\r\n"
                    w.write(resp.encode('utf-8'))
                    w.write(json_bytes)
                else:
                    error_resp = "HTTP/1.1 400 Bad Request\r\nContent-Length: 8\r\n\r\nBad JSON"
                    w.write(error_resp.encode('utf-8'))
                    
            elif path == "/emergency" and method == "POST":
                self.stop = True
                self.q = []
                self.is_playing = False
                self.playback_queue = []
                print("STOP!")
                
                json_resp = '{"ok":true}'
                json_bytes = json_resp.encode('utf-8')
                resp = h + "Content-Type: application/json\r\nContent-Length: " + str(len(json_bytes)) + "\r\n\r\n"
                w.write(resp.encode('utf-8'))
                w.write(json_bytes)
                
            elif path == "/recording" and method == "POST":
                success = self.save_recording(body)
                if success:
                    resp_body = '{"ok":true}'
                    resp_bytes = resp_body.encode('utf-8')
                    resp = "HTTP/1.1 200 OK\r\n" + h.split('\r\n', 1)[1] + "Content-Type: application/json\r\nContent-Length: " + str(len(resp_bytes)) + "\r\n\r\n"
                    w.write(resp.encode('utf-8'))
                    w.write(resp_bytes)
                else:
                    error_body = '{"ok":false}'
                    error_bytes = error_body.encode('utf-8')
                    resp = "HTTP/1.1 500 Internal Server Error\r\nContent-Length: " + str(len(error_bytes)) + "\r\n\r\n"
                    w.write(resp.encode('utf-8'))
                    w.write(error_bytes)
                
            elif path == "/playback" and method == "POST":
                success = self.start_playback(body)
                if success:
                    resp_body = '{"ok":true}'
                    resp_bytes = resp_body.encode('utf-8')
                    resp = "HTTP/1.1 200 OK\r\n" + h.split('\r\n', 1)[1] + "Content-Type: application/json\r\nContent-Length: " + str(len(resp_bytes)) + "\r\n\r\n"
                    w.write(resp.encode('utf-8'))
                    w.write(resp_bytes)
                else:
                    error_body = '{"ok":false}'
                    error_bytes = error_body.encode('utf-8')
                    resp = "HTTP/1.1 400 Bad Request\r\nContent-Length: " + str(len(error_bytes)) + "\r\n\r\n"
                    w.write(resp.encode('utf-8'))
                    w.write(error_bytes)
                
            else:
                error_body = "404 Not Found"
                error_bytes = error_body.encode('utf-8')
                resp = "HTTP/1.1 404 Not Found\r\nContent-Length: " + str(len(error_bytes)) + "\r\n\r\n"
                w.write(resp.encode('utf-8'))
                w.write(error_bytes)
                
            await w.drain()
            
        except Exception as e:
            print("Handler error: " + str(e))
        finally:
            try:
                await w.wait_closed()
            except:
                pass
            # Clean up after each request
            gc.collect()
    
    def handle_joint_cmd(self, body):
        """Handle joint command with minimal memory usage"""
        if self.stop or not body:
            return True
            
        try:
            print("Body: " + body)
            
            # Simple JSON parsing to save memory
            if '"joint_move"' in body and '"joint":' in body and '"angle":' in body:
                # Extract joint number
                joint_start = body.find('"joint":') + 8
                joint_end = body.find(',', joint_start)
                if joint_end == -1:
                    joint_end = body.find('}', joint_start)
                joint = int(body[joint_start:joint_end].strip())
                
                # Extract angle
                angle_start = body.find('"angle":') + 8
                angle_end = body.find(',', angle_start)
                if angle_end == -1:
                    angle_end = body.find('}', angle_start)
                angle = int(body[angle_start:angle_end].strip())
                
                if 0 <= joint < self.dof:
                    self.q.append((joint, angle))
                    print("CMD: J" + str(joint) + "=" + str(angle))
                    return True
            
            return False
            
        except Exception as e:
            print("Parse error: " + str(e))
            return False
    
    def save_recording(self, body):
        """Save recording with minimal memory"""
        try:
            # Simple parsing for filename and movements
            if '"filename":' in body and '"movements":' in body:
                # Extract filename (simplified)
                fn_start = body.find('"filename":"') + 12
                fn_end = body.find('"', fn_start)
                filename = body[fn_start:fn_end]
                if len(filename) > 20:
                    filename = "rec.json"
                
                print("Saving to: " + filename)
                
                # For now, just create empty file to save memory
                # In real implementation, you'd parse movements array
                with open(filename, 'w') as f:
                    f.write('[]')
                
                return True
                
        except Exception as e:
            print("Save error: " + str(e))
            
        return False
    
    def start_playback(self, body):
        """Start playback with minimal parsing"""
        try:
            # Simplified playback - just clear queues for now
            self.q = []
            self.is_playing = False  # Set to True when you have movements to play
            self.playback_queue = []
            print("Playback ready")
            return True
            
        except Exception as e:
            print("Playback error: " + str(e))
            return False
    
    async def servo_task(self):
        """Servo control loop"""
        while True:
            try:
                # Handle playback
                if self.is_playing and self.playback_queue:
                    joint, angle, delay = self.playback_queue.pop(0)
                    if joint < len(self.servos):
                        self.servos[joint].move(angle)
                        print("Play: J" + str(joint) + "=" + str(angle))
                        await asyncio.sleep(delay / 1000.0)
                    
                    if not self.playback_queue:
                        self.is_playing = False
                        print("Playback done")
                
                # Handle normal commands
                elif not self.stop and self.q:
                    joint, angle = self.q.pop(0)
                    if joint < len(self.servos):
                        self.servos[joint].move(angle)
                        print("Move: J" + str(joint) + "=" + str(angle))
                        
                        if self.led:
                            self.led.on()
                            await asyncio.sleep(0.02)
                            self.led.off()
                
                # Auto-clear emergency
                if self.stop:
                    await asyncio.sleep(2)
                    self.stop = False
                    print("Emergency cleared")
                    
                await asyncio.sleep(0.05)
                
            except Exception as e:
                print("Servo error: " + str(e))
                await asyncio.sleep(0.1)
    
    async def mem_task(self):
        """Memory management"""
        count = 0
        while True:
            try:
                # Aggressive cleanup
                if gc.mem_free() < 10000:
                    gc.collect()
                    
                count = (count + 1) % 15  # Every 30 seconds
                if count == 0:
                    print("Mem: " + str(gc.mem_free()))
                    if self.led:
                        self.led.on()
                        await asyncio.sleep(0.1)
                        self.led.off()
                    
                await asyncio.sleep(2)
                
            except:
                await asyncio.sleep(1)
    
    async def run(self):
        print("ARM CTRL v2")
        print("Platform: " + ("ESP32" if IS_ESP32 else "ESP8266"))
        print("Mem: " + str(gc.mem_free()))
        
        if not await self.wifi_setup():
            return
        
        try:
            server = await asyncio.start_server(self.handle_req, "0.0.0.0", 80)
            print("HTTP OK")
        except Exception as e:
            print("Server error: " + str(e))
            return
        
        # Start tasks
        t1 = asyncio.create_task(self.servo_task())
        t2 = asyncio.create_task(self.mem_task())
        
        print("READY! DOF=" + str(self.dof))
        
        try:
            await asyncio.gather(t1, t2)
        except Exception as e:
            print("Error: " + str(e))

# Entry point
async def main():
    ctrl = ArmCtrl()
    await ctrl.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print("Boot error: " + str(e))
        import time
        time.sleep(2)
        import machine
        machine.reset()