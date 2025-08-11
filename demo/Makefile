CC = gcc
CFLAGS = -Wall -Wextra -std=c11
TARGET = demo

all: $(TARGET)

TARGET = demo
SRC = $(TARGET).c

$(TARGET): $(SRC)
	gcc -o $(TARGET) $(SRC)

clean:
	rm -f $(TARGET)
