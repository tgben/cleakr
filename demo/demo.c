#include <stdio.h>
#include <stdlib.h>

void foo() {
  char *bad = malloc(2048);
}

int main() {
    foo();
    return 0;
}
