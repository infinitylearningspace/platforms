from machine import Pin, PWM, ADC
import time

# Servo pins (GPIO)
servo_pins = [14, 12, 13, 15]

# Potentiometer pins (ADC)
pot_pins = [36, 39, 32, 33]  # Using ADC1 pins for D1, D2, D4, D5

# Create servo PWM objects
servos = []
for pin in servo_pins:
    servo = PWM(Pin(pin))
    servo.freq(50)  # 50Hz for servo control
    servos.append(servo)

# Create ADC objects for potentiometers
pots = []
for pin in pot_pins:
    adc = ADC(Pin(pin))
    adc.atten(ADC.ATTN_11DB)  # Full range: 3.3V
    pots.append(adc)

def angle_to_duty(angle):
    """Convert angle (0-180) to PWM duty cycle"""
    # Servo pulse width: 0.5ms (0°) to 2.5ms (180°)
    # For 50Hz: 20ms period, so duty cycle = pulse_width / 20ms * 1024
    pulse_width = 0.5 + (angle / 180.0) * 2.0  # 0.5ms to 2.5ms
    duty = int((pulse_width / 20.0) * 1024)
    return duty

def read_pot_angle(pot_index):
    """Read potentiometer and convert to servo angle (0-180)"""
    pot_value = pots[pot_index].read()  # 0-4095 (12-bit ADC)
    angle = int((pot_value / 4095.0) * 180)  # Map to 0-180 degrees
    return angle

def main():
    print("Servo Control with Potentiometers")
    print("Servo pins:", servo_pins)
    print("Potentiometer pins:", pot_pins)
    print("Starting control loop...")
    
    try:
        while True:
            # Read all potentiometers and update servos
            for i in range(4):
                angle = read_pot_angle(i)
                duty = angle_to_duty(angle)
                servos[i].duty(duty)
                
                # Print status for debugging
                pot_raw = pots[i].read()
                print(f"Servo {i+1}: Pot={pot_raw:4d}, Angle={angle:3d}°, Duty={duty:3d}")
            
            print("-" * 50)
            time.sleep(0.1)  # Update every 100ms
            
    except KeyboardInterrupt:
        print("\nStopping servo control...")
        # Stop all servos
        for servo in servos:
            servo.deinit()
        print("Servos stopped.")

if __name__ == "__main__":
    main()