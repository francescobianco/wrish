# wrish


reset bluetooth ubuntu
```shell

$ rfkill unblock all

```



nuova tecnica basata su gatttool

```
(echo "connect A4:C1:38:9A:A8:2C"; sleep 15; echo "char-read-uuid 00002a00-0000-1000-8000-00805f9b34fb"; sleep 3) | script -q -c "gatttool -I" /dev/null
```
