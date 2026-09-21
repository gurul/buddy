#include "../src/expression.h"
#include <cassert>
#include <cstdio>
int main() {
  using namespace expression;
  assert(parse("happy") == Happy && parse("invalid") == None && parse(nullptr) == None);
  State s;
  assert(s.accept({1, Happy, 4000}, 100));
  assert(s.active(99) && s.active(4099) && !s.active(4100));
  assert(!s.accept({1, Curious, 4000}, 200));
  assert(!s.accept({0, Curious, 4000}, 200));
  assert(s.accept({5, None, 4000}, 11000) && !s.active(11000));
  assert(s.accept({6, Happy, 0}, 12000) && s.request.ttl == 500);
  assert(s.accept({7, Happy, 99999}, 12000) && s.request.ttl == 6000);
  assert(allowed(false, false, false, false, false));
  for (int i=0; i<5; i++) assert(!allowed(i==0,i==1,i==2,i==3,i==4));
  State wrap;
  assert(wrap.accept({0xfffffffe, Happy, 500}, 0xffffff00));
  assert(wrap.active(100) && !wrap.active(300));
  assert(wrap.accept({1, Curious, 4000}, 300));
  assert(!wrap.accept({0xfffffffe, Happy, 4000}, 301));
  puts("EXPRESSION_FIRMWARE_OK");
}
