/* * Infrasound Lab v17.3 - Gain 64x Single Board (Seismic Only)
 * Seismic Channel (Module 2): DOUT = PB8, SCK = PB9
 */

// Seismic Channel Configuration
const int DOUT_S = PB8; 
const int SCK_S  = PB9; 

// Dummy placeholder for unused pressure channel
const int32_t DUMMY_PRESSURE_VAL = 0; 

void setup() {
  Serial.begin(500000);
  
  pinMode(SCK_S, OUTPUT);
  pinMode(DOUT_S, INPUT_PULLUP);
  
  // Power-on Reset for seismic module
  digitalWrite(SCK_S, HIGH);
  delayMicroseconds(100);
  digitalWrite(SCK_S, LOW);
}

void loop() {
  int32_t valS = 0;

  // 1. Wait for sensor to pull DOUT low (Ready) with a 200ms timeout fail-safe
  uint32_t startTime = millis();
  while (digitalRead(DOUT_S) == HIGH) {
    if (millis() - startTime > 200) { 
      Serial.println("ERROR: Sensor Timeout. Check wiring.");
      delay(500); 
      return;
    }
  }

  // Disable interrupts during timing-critical bit bang
  noInterrupts();

  // 2. Read 24 bits
  for (int i = 0; i < 24; i++) {
    digitalWrite(SCK_S, HIGH);
    delayMicroseconds(1);
    
    digitalWrite(SCK_S, LOW);
    delayMicroseconds(1);
    
    valS = (valS << 1) | digitalRead(DOUT_S);
  }

  // 3. SET GAIN 64 (Total 27 pulses = 24 data + 3 extra pulses)
  for (int i = 0; i < 3; i++) {
    digitalWrite(SCK_S, HIGH);
    delayMicroseconds(1);
    digitalWrite(SCK_S, LOW);
    delayMicroseconds(1);
  }

  interrupts();

  // 4. Handle 2's complement sign extension
  if (valS & 0x800000) valS |= 0xFF000000;

  // 5. Output to Python Lab (maintains standard CSV frame layout)
  static uint32_t count = 0;
  Serial.print(count++);
  Serial.print(",80,");
  Serial.print(DUMMY_PRESSURE_VAL); // Template value placeholder
  Serial.print(",");
  Serial.println(valS);
}