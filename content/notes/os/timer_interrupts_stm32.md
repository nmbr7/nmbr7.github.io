+++
title = "Timer Interrupts STM32"
date = 2026-02-10
[taxonomies]
tags = ["stm32", "interrupts", "embedded", "arm-cortex", "timers", "nvic", "pwm"]
+++


# Timer Interrupts in STM32 Chips

## Hardware Architecture

```
HSI/HSE Crystal (8-25 MHz)
        ↓
    PLL (multiply/divide)
        ↓
    System Clock (SYSCLK) → up to 72-480 MHz
        ↓
    APB Bus Prescaler
        ↓
    Timer Peripheral (TIM1, TIM2, etc.)
        ↓
    NVIC → CPU Interrupt
```

## Timer Types

| Timer     | Bits   | Features                     |
| --------- | ------ | ---------------------------- |
| SysTick   | 24-bit | ARM core timer, OS ticks     |
| TIM1/TIM8 | 16-bit | Advanced, PWM, motor control |
| TIM2/TIM5 | 32-bit | General purpose              |
| TIM3/TIM4 | 16-bit | General purpose              |
| TIM6/TIM7 | 16-bit | Basic, DAC triggers          |

## SysTick (RTOS Tick Timer)

Built into ARM Cortex-M core:

```c
// Configure 1ms tick at 72MHz
SysTick->LOAD = 72000 - 1;    // Reload value
SysTick->VAL = 0;              // Clear current
SysTick->CTRL = 0x07;          // Enable, interrupt, use CPU clock
```

```
CTRL register:
  bit 0: ENABLE
  bit 1: TICKINT (enable interrupt)
  bit 2: CLKSOURCE (0=AHB/8, 1=AHB)
```

## General Timer Operation

```c
// TIM2 example - 1 second interrupt at 72MHz
RCC->APB1ENR |= RCC_APB1ENR_TIM2EN;  // Enable clock

TIM2->PSC = 7200 - 1;    // Prescaler: 72MHz / 7200 = 10kHz
TIM2->ARR = 10000 - 1;   // Auto-reload: 10kHz / 10000 = 1Hz
TIM2->DIER = TIM_DIER_UIE;  // Update interrupt enable
TIM2->CR1 = TIM_CR1_CEN;    // Counter enable

NVIC_EnableIRQ(TIM2_IRQn);
```

## Interrupt Flow

```
Timer counter (CNT)
        ↓
Counts up each clock tick
        ↓
CNT == ARR (auto-reload register)?
        ↓
Set UIF flag (update interrupt flag)
        ↓
If DIER.UIE set → trigger NVIC
        ↓
NVIC interrupts CPU
        ↓
CPU jumps to TIM2_IRQHandler()
```

## IRQ Handler

```c
void TIM2_IRQHandler(void) {
    if (TIM2->SR & TIM_SR_UIF) {
        TIM2->SR &= ~TIM_SR_UIF;  // Clear flag

        // Your code here
        jiffies++;
    }
}
```

## Clock Tree Example (STM32F4)

```
HSE 8MHz
    ↓
PLL ×42 ÷2 = 168 MHz (SYSCLK)
    ↓
AHB prescaler /1 = 168 MHz
    ↓
APB1 prescaler /4 = 42 MHz (timers get ×2 = 84 MHz)
APB2 prescaler /2 = 84 MHz (timers get ×2 = 168 MHz)
```

## Check Timer Clock

```c
// HAL way
uint32_t tim_clk = HAL_RCC_GetPCLK1Freq();
if (RCC->CFGR & RCC_CFGR_PPRE1) {
    tim_clk *= 2;  // Timer gets 2x if APB prescaler > 1
}
```

## Key Registers

| Register | Function                    |
| -------- | --------------------------- |
| CR1      | Control (enable, direction) |
| PSC      | Prescaler                   |
| ARR      | Auto-reload (period)        |
| CNT      | Current count               |
| SR       | Status flags                |
| DIER     | Interrupt enable            |

---

## See Also

- [Cycle Counters and Energy](@/notes/hardware/cycle_counters_and_energy.md) — Cycle counter mechanics and per-cycle energy analysis relevant to timer hardware
- [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) — System timer instructions (RDTSC, CNTVCT_EL0, rdtime) across x86/ARM/RISC-V
